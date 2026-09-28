"""Motor de imagen de Martec Camara.

Captura la camara real, detecta la cara, recorta una ventana alrededor que la
sigue (eso es el zoom), suaviza la piel y manda el resultado a la camara
virtual. Corre en su propio hilo; la ventana solo lee su estado y le pasa
ajustes.

Camara virtual:
  - Windows: driver propio "Martec Camara" (softcam personalizado, MIT). Dice si
    una app esta conectada, asi la webcam real se abre solo cuando hace falta.
  - Mac: OBS Virtual Camera a traves del programa aparte puente-vcam (GPL-2.0).

Por que el zoom es por software: muchas webcams declaran Pan/Tilt/Zoom en el
firmware pero no lo aplican (se midio en una Microsoft LifeCam Cinema: el
encuadre sale identico con zoom 0 y con zoom 10). Recortar funciona siempre.
"""

import ctypes
import os
import subprocess
import threading
import time

import cv2
import numpy as np

from . import sistema

# Tamano fijo de la camara virtual (16:9, multiplo de 4 como pide el driver).
OUT_W, OUT_H = 1280, 720
ASPECTO = OUT_W / OUT_H

ENCUADRE_MIN, ENCUADRE_MAX = 2.5, 6.0
AIRE_MIN, AIRE_MAX = 0.2, 1.5
# Suavizado exponencial del recorte: bajo = movimiento lento y fluido.
SUAVE = 0.10
# Desvio tolerado sin recolocar, en fraccion del ancho del recorte.
DEADZONE = 0.05
# Detectar cara cada N cuadros; entre detecciones el recorte sigue deslizandose.
DETECTAR_CADA = 2
ESCALA_DET = 0.5
# Detector de caras: YuNet (modelo MIT de Shiqi Yu, incluido en OpenCV desde
# 4.5.4) sigue la cara aunque este de lado y casi no confunde otras cosas con
# una cara. Haar, el de antes, solo la ve de frente: al girar la cara la perdia
# o saltaba a falsos positivos y el zoom se volvia loco. Queda de respaldo.
MODELO_YUNET = "face_detection_yunet_2023mar.onnx"
YUNET_ANCHO = 320            # sobra para la cara de una videollamada: ~4 ms por cuadro
YUNET_MIN = 0.6
# Los ajustes (encuadre en anchos de cara, aire en alturas de cara) se
# calibraron con la caja de Haar, y la de YuNet es mas angosta: se convierte.
# Medido con la LifeCam: lado Haar = 0.97 x alto YuNet, arriba Haar = arriba
# YuNet + 0.04 x alto. Se usa el alto porque casi no cambia al girar la cara.
HAAR_POR_ALTO = 0.97
HAAR_ARRIBA = 0.04
# Una cara lejos de la que se venia siguiendo (otra persona, un falso
# positivo) se toma solo si se repite en estas detecciones seguidas.
SALTO_MAX = 1.0              # lejania, en lados de cara (ver parecido())
SALTO_CONFIRMAR = 3
# Suavizado del tamano de la cara detectada (0-1, bajo = mas estable): un
# tamano que tiembla es un zoom que tiembla.
SUAVE_TAM = 0.3
# Sin ver la cara hasta estos segundos se sostiene la ultima; despues el
# encuadre se queda quieto.
SOSTENER_S = 0.8
# Sin cara durante estos segundos, el encuadre se abre al cuadro completo
# (si no, se queda congelado donde la perdio, por ejemplo en una pared).
PERDIDA_S = 3.0
# Imagen negra durante estos segundos: con pocos cuadros por segundo es que
# otra app tiene la camara (Windows entrega negro a ~1 cps); con cuadros
# normales la escena esta oscura y se sigue transmitiendo.
NEGRO_S = 5.0
FPS_OCUPADA = 4.0
# Segundos sin que nadie use la camara virtual ni mire la vista previa antes
# de soltar la webcam real.
LIBERAR_S = 4.0
VISTA_W = 480


# ------------------------------------------------------------ imagen

def cargar_haar():
    casc = []
    for nombre in ("haarcascade_frontalface_default.xml",
                   "haarcascade_frontalface_alt2.xml"):
        c = cv2.CascadeClassifier(sistema.ruta_recurso(nombre))
        if not c.empty():
            casc.append(c)
    return casc


class Detector:
    """Caras del cuadro como cajas estilo Haar [x, y, lado, lado], en
    coordenadas del cuadro completo."""

    def __init__(self):
        self.yunet, self.haar, self._entrada = None, [], None
        try:
            self.yunet = cv2.FaceDetectorYN.create(sistema.ruta_recurso(MODELO_YUNET), "",
                                                   (YUNET_ANCHO, 180), YUNET_MIN, 0.3, 20)
        except (cv2.error, AttributeError) as e:
            sistema.log(f"motor: YuNet no disponible ({e!r}); se usa Haar")
            self.haar = cargar_haar()
        self.nombre = "YuNet" if self.yunet else ("Haar" if self.haar else "")

    def __bool__(self):
        return bool(self.nombre)

    def caras(self, frame, gris_eq):
        """gris_eq: el cuadro a ESCALA_DET, en gris y ecualizado (solo Haar)."""
        if self.yunet is None:
            for c in self.haar:
                caras = c.detectMultiScale(gris_eq, scaleFactor=1.1, minNeighbors=5,
                                           minSize=(int(60 * ESCALA_DET), int(60 * ESCALA_DET)))
                if len(caras) > 0:
                    return [np.array(r, dtype=np.float64) / ESCALA_DET for r in caras]
            return []
        H, W = frame.shape[:2]
        entrada = (YUNET_ANCHO, max(1, round(YUNET_ANCHO * H / W)))
        if entrada != self._entrada:
            self.yunet.setInputSize(entrada)
            self._entrada = entrada
        _, caras = self.yunet.detect(cv2.resize(frame, entrada, interpolation=cv2.INTER_AREA))
        if caras is None:
            return []
        e = W / entrada[0]
        res = []
        for c in caras:
            x, y, w, h = (float(v) * e for v in c[:4])
            lado = h * HAAR_POR_ALTO
            res.append(np.array([x + w / 2 - lado / 2, y + HAAR_ARRIBA * h, lado, lado]))
        return res


def parecido(a, b):
    """Que tan distinta es la caja a de la b: distancia entre centros en lados
    de b, mas la diferencia de tamano (0 = igual; 1 = un lado de cara de distancia
    o casi el triple de tamano)."""
    d = np.hypot(a[0] + a[2] / 2 - b[0] - b[2] / 2, a[1] + a[3] / 2 - b[1] - b[3] / 2)
    return d / b[2] + abs(np.log(a[2] / b[2]))


def caja_completa(W, H):
    """El mayor recorte 16:9 centrado en el cuadro (evita deformar la imagen
    cuando la camara es 4:3)."""
    if W / H > ASPECTO:
        cw, ch = H * ASPECTO, float(H)
    else:
        cw, ch = float(W), W / ASPECTO
    return (W - cw) / 2, (H - ch) / 2, cw, ch


def tam_recorte(ancho_cara, W, H, factor):
    cw = float(np.clip(ancho_cara * factor, 160, W))
    ch = cw / ASPECTO
    if ch > H:
        ch = float(H)
        cw = ch * ASPECTO
    return cw, ch


def recorte_para(cx, arriba, ancho_cara, W, H, factor):
    """Recorte 16:9 centrado en cx, con su borde superior en 'arriba'."""
    cw, ch = tam_recorte(ancho_cara, W, H, factor)
    x = float(np.clip(cx - cw / 2, 0, W - cw))
    y = float(np.clip(arriba, 0, H - ch))
    return x, y, cw, ch


def suavizar_piel(frame, cara, fuerza):
    """Alisa la piel de la cara sin tocar ojos, cejas, barba ni fondo.

    Filtro bilateral (suaviza conservando bordes) sobre la zona de la cara a
    media resolucion, aplicado solo donde el color es de piel (YCrCb), con la
    mascara difuminada para que no se note el borde. Modifica frame.
    """
    H, W = frame.shape[:2]
    x, y, w, h = cara
    x0, x1 = int(max(0, x - 0.3 * w)), int(min(W, x + 1.3 * w))
    y0, y1 = int(max(0, y - 0.45 * h)), int(min(H, y + 1.35 * h))
    if x1 - x0 < 32 or y1 - y0 < 32:
        return
    roi = frame[y0:y1, x0:x1]
    chico = cv2.resize(roi, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    chico = cv2.bilateralFilter(chico, d=7, sigmaColor=45, sigmaSpace=7)
    liso = cv2.resize(chico, (roi.shape[1], roi.shape[0]),
                      interpolation=cv2.INTER_LINEAR)
    ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
    piel = cv2.inRange(ycc, (40, 135, 80), (255, 175, 128))
    piel = cv2.GaussianBlur(piel, (0, 0), max(2.0, w / 40.0))
    alfa = (piel.astype(np.float32) * (fuerza / 255.0))[..., None]
    roi[:] = (roi.astype(np.float32) * (1.0 - alfa)
              + liso.astype(np.float32) * alfa).astype(np.uint8)


def imagen_espera():
    """Cuadro que ve una app mientras la webcam real se esta abriendo."""
    im = np.full((OUT_H, OUT_W, 3), (60, 30, 12), np.uint8)
    for texto, escala, y in (("Martec Camara", 2.0, OUT_H // 2 - 10),
                             ("Encendiendo la camara...", 1.0, OUT_H // 2 + 50)):
        (tw, _), _ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, 2)
        cv2.putText(im, texto, ((OUT_W - tw) // 2, y), cv2.FONT_HERSHEY_SIMPLEX,
                    escala, (240, 240, 240), 2, cv2.LINE_AA)
    return im


# ------------------------------------------------------------ salidas

class SalidaSoftcam:
    """Windows: driver propio "Martec Camara" (softcam personalizado, MIT).

    La misma DLL que el instalador registra en Windows expone la API para
    enviar cuadros: BGR de 24 bits, de arriba hacia abajo, OUT_W x OUT_H.
    """

    def __init__(self):
        self.dll = None
        self.cam = None
        self.estado = "iniciando"       # ok | sin_driver
        self.detalle = ""

    def abrir(self):
        if self.cam:
            return True
        if self.dll is None:
            try:
                dll = ctypes.CDLL(sistema.ruta_driver())
            except OSError as e:
                self.estado, self.detalle = "sin_driver", f"no se pudo cargar el driver ({e})"
                return False
            dll.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float]
            dll.scCreateCamera.restype = ctypes.c_void_p
            dll.scDeleteCamera.argtypes = [ctypes.c_void_p]
            dll.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            dll.scIsConnected.argtypes = [ctypes.c_void_p]
            dll.scIsConnected.restype = ctypes.c_bool
            self.dll = dll
        # framerate 0: cada cuadro se entrega al llegar (la fuente es una webcam).
        self.cam = self.dll.scCreateCamera(OUT_W, OUT_H, 0.0)
        if not self.cam:
            self.estado = "sin_driver"
            self.detalle = "otra copia de Martec Cámara ya está usando la cámara virtual"
            return False
        if sistema.driver_registrado():
            self.estado, self.detalle = "ok", sistema.NOMBRE_VCAM
        else:
            self.estado = "sin_driver"
            self.detalle = "el driver de la cámara virtual no está instalado"
        sistema.log(f"salida: camara virtual creada ({self.estado})")
        return True

    def conectada(self):
        return bool(self.cam) and bool(self.dll.scIsConnected(self.cam))

    def enviar(self, bgr):
        if self.cam:
            cuadro = np.ascontiguousarray(bgr)
            self.dll.scSendFrame(self.cam, cuadro.ctypes.data)
        return self.estado

    def cerrar(self):
        if self.cam:
            self.dll.scDeleteCamera(self.cam)
            self.cam = None


class Puente:
    """Mac: programa aparte que publica en OBS Virtual Camera.

    Es un programa separado a proposito: usa pyvirtualcam, que es GPL-2.0, y
    asi la app principal no queda mezclada con esa licencia. Recibe los
    cuadros en I420 por la entrada estandar. No sabe si hay una app conectada.
    """

    REINTENTO_S = 10

    def __init__(self):
        self.proc = None
        self.estado = "iniciando"     # iniciando | ok | sin_obs
        self.detalle = ""
        self._proximo = 0.0

    def abrir(self):
        return True                   # el proceso se lanza al enviar el primer cuadro

    def conectada(self):
        return None                   # desconocido: la webcam queda siempre abierta

    def _arrancar(self):
        cmd = sistema.comando_puente() + [str(OUT_W), str(OUT_H), "30"]
        flags = subprocess.CREATE_NO_WINDOW if sistema.ES_WIN else 0
        # Los errores del puente van a su propio registro.
        try:
            err = open(os.path.join(sistema.dir_datos(), "puente.log"), "ab")
        except OSError:
            err = subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=err, creationflags=flags)
        except OSError as e:
            self.proc = None
            self.estado, self.detalle = "sin_obs", f"no se pudo abrir el puente: {e}"
            self._proximo = time.time() + self.REINTENTO_S
            sistema.log(f"puente: {self.detalle}")
            return
        finally:
            if err is not subprocess.DEVNULL:
                err.close()             # el hijo ya tiene su propia copia
        self.estado = "iniciando"
        threading.Thread(target=self._leer, args=(self.proc,), daemon=True).start()

    def _leer(self, proc):
        for crudo in proc.stdout:
            linea = crudo.decode("utf-8", "replace").strip()
            if linea.startswith("LISTO"):
                self.estado, self.detalle = "ok", linea[6:]
                sistema.log(f"puente: camara virtual lista ({self.detalle})")
            elif linea.startswith("ERROR"):
                self.estado, self.detalle = "sin_obs", linea[6:]
                sistema.log(f"puente: {self.detalle}")

    def enviar(self, bgr):
        ahora = time.time()
        if self.proc is None:
            if ahora >= self._proximo:
                self._arrancar()
            return self.estado
        if self.proc.poll() is not None:
            self.proc = None
            self._proximo = ahora + self.REINTENTO_S
            if self.estado != "sin_obs":
                self.estado = "sin_obs"
                self.detalle = self.detalle or "el puente se cerró"
            return self.estado
        if self.estado != "ok":
            return self.estado
        try:
            self.proc.stdin.write(cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).tobytes())
        except OSError:
            self.cerrar()
            self._proximo = ahora + self.REINTENTO_S
            self.estado = "sin_obs"
        return self.estado

    def cerrar(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            p.kill()


class _CabeceraMF(ctypes.Structure):
    """Misma disposicion que MartecFrameHeader en driver_mf (64 bytes)."""
    _pack_ = 8
    _fields_ = [("magic", ctypes.c_uint32), ("version", ctypes.c_uint32),
                ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("stride", ctypes.c_uint32), ("format", ctypes.c_uint32),
                ("seq", ctypes.c_int64), ("writerTick", ctypes.c_int64),
                ("readerTick", ctypes.c_int64), ("reserved", ctypes.c_uint64 * 2)]


class SalidaMF:
    """Windows 11: camara virtual moderna (Media Foundation), la que ven las
    apps de la Microsoft Store como WhatsApp o la Camara de Windows.

    La app crea la camara para el usuario actual mientras esta abierta (no hace
    falta administrador; el instalador solo registra la DLL). El driver
    (driver_mf, derivado de VCamSample) abre la memoria compartida
    "Global\\MartecCamaraMF" cuando una app empieza a usar la camara, y lee de
    ahi cada cuadro BGRA.
    """

    MAGIC = 0x4D41434D
    NOMBRES = ("Global\\MartecCamaraMF", "Local\\MartecCamaraMF")
    FRESCO_MS = 2000       # el driver pidio un cuadro hace menos de esto
    SOLTAR_MS = 3000       # sin pedidos del driver: soltar la memoria compartida
    REINTENTO_S = 5.0      # si crear la camara fallo, volver a probar cada tanto

    def __init__(self):
        self.vcam = None
        self.mapa = None
        self.vista = None
        self.cab = None
        self.estado = "iniciando"
        self.detalle = ""
        self._fresco = False
        self._reintento = 0.0
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.OpenFileMappingW.restype = ctypes.c_void_p
        self._k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
        self._k32.MapViewOfFile.restype = ctypes.c_void_p
        self._k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                            ctypes.c_uint32, ctypes.c_size_t]
        self._k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self._k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._k32.GetTickCount64.restype = ctypes.c_uint64

    @staticmethod
    def _vtabla(ptr, indice, *argtypes):
        vt = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vt[indice])

    def abrir(self):
        """Crea la camara virtual moderna. Solo Windows 11 con la DLL registrada."""
        if self.vcam:
            return True
        if self.estado == "no_disponible":
            return False
        if self.estado == "fallo" and time.time() < self._reintento:
            return False
        if not sistema.driver_mf_registrado():
            self.estado, self.detalle = "no_disponible", "driver moderno no instalado"
            return False
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)            # COINIT_MULTITHREADED
            ctypes.windll.mfplat.MFStartup(0x00020070, 0)          # MF_VERSION, MFSTARTUP_FULL
            crear = ctypes.WinDLL("mfsensorgroup").MFCreateVirtualCamera
        except (OSError, AttributeError) as e:
            self.estado, self.detalle = "no_disponible", f"Windows sin cámaras virtuales modernas ({e})"
            sistema.log(f"salida MF: {self.detalle}")
            return False
        crear.restype = ctypes.c_long
        crear.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p,
                          ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_void_p)]
        vcam = ctypes.c_void_p()
        # Tipo 0 = software, vida 0 = mientras esta app la tenga, acceso 0 = usuario actual.
        hr = crear(0, 0, 0, sistema.NOMBRE_VCAM, sistema.CLSID_VCAM_MF, None, 0, ctypes.byref(vcam))
        if hr < 0 or not vcam.value:
            self._fallo(f"MFCreateVirtualCamera 0x{hr & 0xFFFFFFFF:08X}")
            return False
        hr = self._vtabla(vcam.value, 36, ctypes.c_void_p)(vcam.value, None)   # Start
        if hr < 0:
            self._vtabla(vcam.value, 2)(vcam.value)                              # Release
            self._fallo(f"IMFVirtualCamera::Start 0x{hr & 0xFFFFFFFF:08X}")
            return False
        self.vcam = vcam.value
        self.estado, self.detalle = "ok", sistema.NOMBRE_VCAM
        sistema.log("salida MF: camara virtual moderna creada")
        return True

    def _fallo(self, detalle):
        # Puede ser pasajero (al iniciar sesion, o si una copia anterior todavia
        # se esta cerrando). En Windows 11 es la unica camara: se reintenta.
        self.estado, self.detalle = "fallo", detalle
        self._reintento = time.time() + self.REINTENTO_S
        sistema.log(f"salida MF: {detalle}")

    def _abrir_mapa(self):
        for nombre in self.NOMBRES:
            h = self._k32.OpenFileMappingW(0x0006, False, nombre)   # FILE_MAP_READ | WRITE
            if h:
                v = self._k32.MapViewOfFile(h, 0x0006, 0, 0, 0)
                if v:
                    cab = _CabeceraMF.from_address(v)
                    if cab.magic == self.MAGIC and (cab.width, cab.height) == (OUT_W, OUT_H):
                        self.mapa, self.vista, self.cab = h, v, cab
                        return True
                    self._k32.UnmapViewOfFile(v)
                self._k32.CloseHandle(h)
        return False

    def _cerrar_mapa(self):
        if self.vista:
            self._k32.UnmapViewOfFile(self.vista)
        if self.mapa:
            self._k32.CloseHandle(self.mapa)
        self.mapa = self.vista = self.cab = None
        self._fresco = False

    def conectada(self):
        """True si una app esta tomando cuadros de la camara moderna."""
        if not self.vcam:
            return False
        if self.cab is None and not self._abrir_mapa():
            self._fresco = False
            return False
        edad = self._k32.GetTickCount64() - self.cab.readerTick
        self._fresco = edad < self.FRESCO_MS
        if edad > self.SOLTAR_MS:
            self._cerrar_mapa()      # para que la memoria se libere cuando el driver la suelte
        return self._fresco

    def enviar(self, bgr):
        if not self._fresco or self.cab is None:
            return self.estado
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        cab = self.cab
        cab.seq += 1                                   # impar: escribiendo
        ctypes.memmove(self.vista + ctypes.sizeof(_CabeceraMF), bgra.ctypes.data, bgra.nbytes)
        cab.writerTick = self._k32.GetTickCount64()
        cab.seq += 1                                   # par: cuadro completo
        return self.estado

    def cerrar(self):
        self._cerrar_mapa()
        if self.vcam:
            v, self.vcam = self.vcam, None
            try:
                self._vtabla(v, 38)(v)                 # Remove
                self._vtabla(v, 43)(v)                 # Shutdown
                self._vtabla(v, 2)(v)                  # Release
            except OSError:
                pass
            if self.estado == "ok":
                self.estado = "iniciando"


class SalidaWindows:
    """Camaras virtuales de Windows.

    En Windows 11, solo la moderna (Media Foundation): la ven todas las apps,
    tambien las de DirectShow como Chrome o Zoom (por el puente del servicio de
    camaras de Windows), y con las dos registradas aparecian dos "Martec Camara"
    en cada lista. Por eso ahi el instalador ya no registra la clasica. En
    Windows 10 va la clasica (DirectShow, softcam). Si estan registradas las dos
    (una instalacion vieja), alimenta las dos."""

    def __init__(self):
        self.clasica = SalidaSoftcam()
        self.moderna = SalidaMF()
        # La clasica si esta registrada, o si no hay moderna: asi, sin ningun
        # driver, el aviso de "falta el driver" es el de siempre.
        self.usa_clasica = sistema.driver_registrado() or not sistema.driver_mf_registrado()
        self.salidas = [self.clasica, self.moderna] if self.usa_clasica else [self.moderna]

    @property
    def estado(self):
        if any(s.estado == "ok" for s in self.salidas):
            return "ok"
        if self.usa_clasica:
            return self.clasica.estado
        return "sin_driver" if self.moderna.estado == "no_disponible" else "iniciando"

    @property
    def detalle(self):
        if self.estado == "ok":
            return sistema.nombre_camara_visible()
        return (self.clasica if self.usa_clasica else self.moderna).detalle

    def abrir(self):
        return any([s.abrir() for s in self.salidas])     # lista: abrir todas

    def reintentar(self):
        if self.usa_clasica and self.clasica.cam is None:
            self.clasica.abrir()
        if self.moderna.vcam is None and self.moderna.estado != "no_disponible":
            self.moderna.abrir()

    def conectada(self):
        self._ult = (self.clasica.conectada() if self.usa_clasica else None,
                     self.moderna.conectada())
        return bool(self._ult[0] or self._ult[1])

    def motivo(self):
        """Que camara reporto conexion en la ultima consulta (para el registro)."""
        a, b = getattr(self, "_ult", (None, False))
        return (f"clasica={a} " if self.usa_clasica else "") + f"moderna={b}"

    def enviar(self, bgr):
        for s in self.salidas:
            s.enviar(bgr)
        return self.estado

    def cerrar(self):
        for s in self.salidas:
            s.cerrar()


def crear_salida():
    return SalidaWindows() if sistema.ES_WIN else Puente()


# ------------------------------------------------------------ motor

class Motor(threading.Thread):

    def __init__(self, ajustes):
        super().__init__(daemon=True, name="motor")
        self._lock = threading.Lock()
        self._aj = dict(ajustes)
        self._salir = threading.Event()
        self._reabrir = threading.Event()
        self._det = Detector()
        self._espera = imagen_espera()
        self.salida = crear_salida()
        # La ventana avisa si esta a la vista: entonces hace falta la webcam
        # para la vista previa aunque ninguna app use la camara virtual.
        self.vista_activa = True
        # Estado que lee la ventana.
        self.estado = "iniciando"
        self.detalle = ""
        self.camara = ""
        self.fps = 0.0
        self.hay_cara = False
        self.app_conectada = False
        self.vista_salida = None      # RGB reducido: lo que ven los demas
        self.vista_original = None    # RGB reducido: camara completa con el recorte marcado

    # --- ajustes desde la ventana
    def ajustes(self):
        with self._lock:
            return dict(self._aj)

    def actualizar(self, **cambios):
        with self._lock:
            reabrir = any(k in cambios and cambios[k] != self._aj.get(k)
                          for k in ("camara", "encendido"))
            self._aj.update(cambios)
        if reabrir:
            self._reabrir.set()

    def detener(self):
        self._salir.set()
        self._reabrir.set()

    def _poner(self, estado, detalle=""):
        if estado != self.estado:
            sistema.log(f"motor: {self.estado} -> {estado}" + (f" ({detalle})" if detalle else ""))
        self.estado, self.detalle = estado, detalle

    def _necesita_webcam(self):
        """La webcam real hace falta si una app usa la camara virtual o si la
        ventana esta a la vista. Si la salida no sabe (Mac), siempre."""
        c = self.salida.conectada()
        self.app_conectada = bool(c)
        necesita = True if c is None else (c or self.vista_activa)
        # Registrar cada cambio y por que: sirve para diagnosticar la webcam
        # que se enciende sin que nadie la use.
        motivo = (self.vista_activa, getattr(self.salida, "motivo", lambda: c)())
        if (necesita, motivo) != getattr(self, "_ult_necesita", None):
            self._ult_necesita = (necesita, motivo)
            sistema.log(f"motor: webcam {'NECESARIA' if necesita else 'no hace falta'} "
                        f"(ventana visible={self.vista_activa}, conexiones={motivo[1]})")
        return necesita

    def _estado_salida(self, oscuro=False):
        s = self.salida
        if s.estado == "ok":
            if oscuro:
                return "oscura", s.detalle
            return ("transmitiendo" if self.app_conectada or s.conectada() is None
                    else "vista_previa"), s.detalle
        if s.estado in ("sin_driver", "sin_obs"):
            return s.estado, s.detalle
        return "iniciando", self.camara

    # --- bucle principal
    def run(self):
        sistema.log(f"motor: detector de caras {self._det.nombre or 'NINGUNO'}")
        try:
            while not self._salir.is_set():
                if not self.ajustes()["encendido"]:
                    self.salida.cerrar()
                    self._poner("apagado")
                    self.vista_salida = self.vista_original = None
                    self._reabrir.wait(0.5)
                    self._reabrir.clear()
                    continue
                self.salida.abrir()
                if not self._necesita_webcam():
                    # Nadie la usa: webcam real libre; la camara virtual
                    # muestra el cuadro de espera.
                    if self.salida.estado == "ok":
                        self._poner("en_espera", self.salida.detalle)
                    else:
                        self._poner(self.salida.estado, self.salida.detalle)
                    self.salida.enviar(self._espera)
                    self.vista_salida = self.vista_original = None
                    self._reabrir.wait(0.5)
                    self._reabrir.clear()
                    continue
                try:
                    self._sesion()
                except Exception as e:
                    sistema.log(f"motor: error {e!r}")
                    self._poner("error", str(e))
                    self._reabrir.wait(3)
                if self._reabrir.is_set():
                    self._reabrir.clear()
        finally:
            self.salida.cerrar()

    def _sesion(self):
        idx, nombre = sistema.elegir_camara(self.ajustes().get("camara"))
        if idx is None:
            self._poner("sin_camara")
            self._reabrir.wait(3)
            return
        self.camara = nombre
        self._poner("iniciando", nombre)
        self.salida.enviar(self._espera)
        cap = cv2.VideoCapture(idx, sistema.backend_captura())
        if not cap.isOpened():
            self._poner("sin_camara", nombre)
            cap.release()
            self._reabrir.wait(3)
            return
        try:
            self._bucle(cap, nombre)
        finally:
            cap.release()

    def _bucle(self, cap, nombre):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        for _ in range(10):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            self._poner("sin_imagen", nombre)
            self._reabrir.wait(3)
            return
        H, W = frame.shape[:2]
        vista_h = int(VISTA_W * H / W)
        sistema.log(f"motor: camara '{nombre}' {W}x{H}")

        rx, ry, rw, rh = caja_completa(W, H)
        ultima_cara = None
        ultima_vista_cara = time.time()
        pendiente, n_pend = None, 0
        negro_desde = None
        oscuro = False
        sin_uso_desde = None
        forzar = 0
        n = 0
        t_fps, n_fps = time.time(), 0
        t_vista = t_uso = 0.0
        aj_prev = None
        self.fps = 0.0

        while not self._salir.is_set() and not self._reabrir.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                self._poner("sin_imagen", nombre)
                return
            ahora = time.time()
            n += 1

            # Soltar la webcam si ya nadie la usa.
            if ahora - t_uso > 0.5:
                t_uso = ahora
                # Si una camara virtual no se pudo crear (p. ej. una copia
                # anterior todavia se estaba cerrando), reintentar.
                if hasattr(self.salida, "reintentar"):
                    self.salida.reintentar()
                if self._necesita_webcam():
                    sin_uso_desde = None
                else:
                    sin_uso_desde = sin_uso_desde or ahora
                    if ahora - sin_uso_desde > LIBERAR_S:
                        sistema.log("motor: nadie usa la camara, se suelta la webcam "
                                    f"(ventana visible={self.vista_activa}, "
                                    f"app conectada={self.app_conectada})")
                        return

            a = self.ajustes()
            factor = float(np.clip(a["encuadre"], ENCUADRE_MIN, ENCUADRE_MAX))
            aire = float(np.clip(a["aire"], AIRE_MIN, AIRE_MAX))
            piel = float(np.clip(a["piel"], 0.0, 1.0))
            # Un ajuste chico puede quedar dentro de la zona muerta: se fuerza
            # el movimiento un rato para que el cambio se vea.
            clave = (factor, aire, a["seguimiento"])
            if clave != aj_prev:
                forzar, aj_prev = 45, clave

            if n % DETECTAR_CADA == 0:
                chico = cv2.resize(frame, None, fx=ESCALA_DET, fy=ESCALA_DET)
                gris = cv2.cvtColor(chico, cv2.COLOR_BGR2GRAY)
                if gris.mean() < 2.0:
                    negro_desde = negro_desde or ahora
                    if ahora - negro_desde > NEGRO_S:
                        if self.fps < FPS_OCUPADA:
                            self._poner("ocupada", nombre)
                            self._reabrir.wait(3)
                            return
                        oscuro = True
                else:
                    negro_desde, oscuro = None, False
                candidatas = []
                if self._det and (a["seguimiento"] or piel > 0):
                    candidatas = self._det.caras(
                        frame, cv2.equalizeHist(gris) if self._det.haar else None)
                if ahora - ultima_vista_cara > PERDIDA_S:
                    ultima_cara = None           # perdida del todo: vale cualquier cara
                # La cara a seguir es la mas parecida a la que se venia siguiendo.
                # Una lejana (otra persona, un falso positivo) solo si se repite.
                cara, salto = None, False
                if candidatas and ultima_cara is None:
                    cara, salto = max(candidatas, key=lambda r: r[2]), True
                elif candidatas:
                    cerca = min(candidatas, key=lambda r: parecido(r, ultima_cara))
                    if parecido(cerca, ultima_cara) < SALTO_MAX:
                        cara, pendiente, n_pend = cerca, None, 0
                    else:
                        lejos = (max(candidatas, key=lambda r: r[2]) if pendiente is None
                                 else min(candidatas, key=lambda r: parecido(r, pendiente)))
                        seguida = pendiente is not None and parecido(lejos, pendiente) < SALTO_MAX
                        n_pend = n_pend + 1 if seguida else 1
                        pendiente = lejos
                        if n_pend >= SALTO_CONFIRMAR:
                            cara, salto, pendiente, n_pend = lejos, True, None, 0
                if cara is not None:
                    if not salto:
                        # El centro tal cual; el tamano, suavizado.
                        lado = ultima_cara[2] + (cara[2] - ultima_cara[2]) * SUAVE_TAM
                        cx, cy = cara[0] + cara[2] / 2, cara[1] + cara[3] / 2
                        cara = np.array([cx - lado / 2, cy - lado / 2, lado, lado])
                    ultima_cara, ultima_vista_cara = cara, ahora
            # Un instante sin verla (un parpadeo del detector) se sostiene la ultima.
            cara = ultima_cara if ahora - ultima_vista_cara < SOSTENER_S else None
            self.hay_cara = cara is not None

            # Objetivo del recorte.
            if a["seguimiento"] and cara is not None:
                x, y, cw_, ch_ = cara
                # Anclado por arriba: el borde superior queda 'aire' alturas
                # de cara sobre la cara, para no cortar la cabeza con ningun
                # zoom. Si cortaria la barbilla, se reduce.
                _, alto = tam_recorte(cw_, W, H, factor)
                aire_ef = max(0.0, min(aire, alto / ch_ - 1.1))
                objetivo = recorte_para(x + cw_ / 2.0, y - aire_ef * ch_, cw_, W, H, factor)
                vel = SUAVE
            elif (not a["seguimiento"]) or ahora - ultima_vista_cara > PERDIDA_S:
                objetivo, vel = caja_completa(W, H), SUAVE * 0.5
            else:
                objetivo, vel = None, 0.0

            if objetivo is not None:
                ox, oy, ow, oh = objetivo
                desvio = max(abs(ox - rx), abs(oy - ry), abs(ow - rw)) / max(rw, 1)
                if forzar > 0:
                    forzar -= 1
                if forzar > 0 or desvio > DEADZONE or cara is None:
                    rx += (ox - rx) * vel
                    ry += (oy - ry) * vel
                    rw += (ow - rw) * vel
                    rh += (oh - rh) * vel

            ix, iy = int(round(rx)), int(round(ry))
            iw = min(max(16, int(round(rw))), W - ix)
            ih = min(max(16, int(round(rh))), H - iy)
            self.recorte = (ix, iy, iw, ih)

            quiere_vista = self.vista_activa and ahora - t_vista > 0.1
            if quiere_vista:
                orig = cv2.resize(frame, (VISTA_W, vista_h))

            if piel > 0 and cara is not None:
                suavizar_piel(frame, cara, piel)

            salida = cv2.resize(frame[iy:iy + ih, ix:ix + iw], (OUT_W, OUT_H),
                                interpolation=cv2.INTER_LINEAR)
            if a["espejo"]:
                salida = cv2.flip(salida, 1)

            self.salida.enviar(salida)
            self._poner(*self._estado_salida(oscuro))

            if quiere_vista:
                t_vista = ahora
                e = VISTA_W / W
                cv2.rectangle(orig, (int(ix * e), int(iy * e)),
                              (int((ix + iw) * e), int((iy + ih) * e)),
                              (0, 200, 255), 2)
                if cara is not None:
                    x, y, cw_, ch_ = cara
                    cv2.rectangle(orig, (int(x * e), int(y * e)),
                                  (int((x + cw_) * e), int((y + ch_) * e)),
                                  (0, 220, 0) if ahora - ultima_vista_cara < 0.25
                                  else (0, 200, 200), 2)
                self.vista_original = cv2.cvtColor(orig, cv2.COLOR_BGR2RGB)
                self.vista_salida = cv2.cvtColor(
                    cv2.resize(salida, (VISTA_W, int(VISTA_W / ASPECTO))), cv2.COLOR_BGR2RGB)

            n_fps += 1
            if ahora - t_fps >= 1.0:
                self.fps = n_fps / (ahora - t_fps)
                t_fps, n_fps = ahora, 0
