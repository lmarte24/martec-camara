"""Todo lo que depende del sistema operativo.

Rutas de datos, configuracion, registro, candado de una sola copia, arranque
con el sistema, lista de camaras y ubicacion del puente de camara virtual.
"""

import json
import os
import platform
import plistlib
import subprocess
import sys
import time
import webbrowser

APP = "MartecCamara"
NOMBRE = "Martec Cámara"
VERSION = "0.1.0"

ES_WIN = platform.system() == "Windows"
ES_MAC = platform.system() == "Darwin"
EMPAQUETADO = getattr(sys, "frozen", False)

URL_OBS = "https://obsproject.com/download"

AJUSTES_DEF = {
    "encendido": True,
    "camara": None,          # nombre de la camara elegida; None = la primera real
    "seguimiento": True,
    "encuadre": 3.6,         # ancho del recorte en anchos de cara (zoom)
    "aire": 0.4,             # espacio sobre la cabeza, en alturas de cara
    "piel": 0.5,             # suavizado de piel, 0 = apagado
    "espejo": False,
    "aviso_bandeja": False,  # ya se mostro el aviso de "sigue en la bandeja"
}


# --------------------------------------------------------------------- rutas

def dir_datos():
    if ES_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif ES_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, APP)
    os.makedirs(d, exist_ok=True)
    return d


def ruta_recurso(nombre):
    """Archivo de la carpeta recursos, tanto en desarrollo como empaquetado."""
    if EMPAQUETADO:
        base = os.path.join(sys._MEIPASS, "recursos")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recursos")
    return os.path.join(base, nombre)


# ------------------------------------------------------ registro y ajustes

def log(msg):
    ruta = os.path.join(dir_datos(), "martec-camara.log")
    try:
        if os.path.exists(ruta) and os.path.getsize(ruta) > 1_000_000:
            os.replace(ruta, ruta + ".1")
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def leer_ajustes():
    a = dict(AJUSTES_DEF)
    try:
        with open(os.path.join(dir_datos(), "ajustes.json"), encoding="utf-8") as fh:
            a.update({k: v for k, v in json.load(fh).items() if k in AJUSTES_DEF})
    except (OSError, ValueError):
        pass
    return a


def guardar_ajustes(a):
    try:
        with open(os.path.join(dir_datos(), "ajustes.json"), "w", encoding="utf-8") as fh:
            json.dump({k: a.get(k) for k in AJUSTES_DEF}, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ------------------------------------------------------ una sola copia

class Candado:
    """Candado de archivo: solo una copia de la app a la vez. Si ya hay otra,
    se le pide que muestre su ventana."""

    def __init__(self):
        self._fh = None
        self._mutex = None

    def tomar(self):
        if ES_WIN:
            # Mutex con nombre: el instalador (AppMutex) lo usa para saber si la
            # app esta abierta y pedir que se cierre antes de actualizar.
            import ctypes
            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "MartecCamaraMutex")
        ruta = os.path.join(dir_datos(), "instancia.lock")
        fh = open(ruta, "a+")
        try:
            if ES_WIN:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True


def pedir_mostrar():
    try:
        open(os.path.join(dir_datos(), "mostrar"), "w").close()
    except OSError:
        pass


def hay_pedido_mostrar():
    ruta = os.path.join(dir_datos(), "mostrar")
    if os.path.exists(ruta):
        try:
            os.remove(ruta)
        except OSError:
            pass
        return True
    return False


# ------------------------------------------------------ camaras

def backend_captura():
    import cv2
    if ES_WIN:
        return cv2.CAP_DSHOW
    if ES_MAC:
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


NOMBRE_VCAM = "Martec Cámara"      # nombre de nuestra camara virtual en Windows
CLSID_VCAM = "{24BE734C-01F4-4D96-83D8-F93DC74CE46C}"


def es_virtual(nombre):
    n = (nombre or "").lower()
    propia = n.startswith("martec cámara") or n.startswith("martec camara")
    return propia or "virtual" in n or "obs" in n.split()


CLSID_VCAM_MF = "{A2A57FD3-003A-42C3-ABF8-A1DA5AB51166}"   # driver moderno (Windows 11)
# Windows le agrega el sufijo a las camaras virtuales modernas; asi aparece en
# Chrome, Meet, Zoom y WhatsApp.
NOMBRE_VCAM_MF = NOMBRE_VCAM + " (Windows Virtual Camera)"


def nombre_camara_visible():
    """Como se llama la camara de Martec en las apps de video: en Windows 11 el
    instalador solo registra la moderna."""
    if ES_WIN and not driver_registrado() and driver_mf_registrado():
        return NOMBRE_VCAM_MF
    return NOMBRE_VCAM


def driver_mf_registrado():
    """True si el driver moderno (Media Foundation) esta registrado. Lo hace el
    instalador solo en Windows 11."""
    if not ES_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SOFTWARE\Classes\CLSID\{CLSID_VCAM_MF}\InprocServer32") as k:
            ruta, _ = winreg.QueryValueEx(k, "")
            return bool(ruta) and os.path.exists(ruta)
    except OSError:
        return False


def ruta_driver():
    """DLL de 64 bits del driver clasico de camara virtual (Windows).

    Se usa la que esta REGISTRADA: cada version del instalador pone sus drivers
    en una carpeta propia (driver\\<compilacion>), para no pisar una DLL que
    Chrome o Teams tengan cargada. Asi la app siempre habla con el mismo driver
    que ven las demas apps."""
    nombre = "MartecCamaraVCam64.dll"
    if ES_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SOFTWARE\Classes\CLSID\{CLSID_VCAM}\InprocServer32") as k:
                ruta, _ = winreg.QueryValueEx(k, "")
                if ruta and os.path.exists(ruta):
                    return ruta
        except OSError:
            pass
    if EMPAQUETADO:
        base = os.path.join(os.path.dirname(sys.executable), "driver")
        candidatas = [os.path.join(base, d, nombre) for d in os.listdir(base)] \
            if os.path.isdir(base) else []
        candidatas = sorted((c for c in candidatas if os.path.exists(c)), reverse=True)
        return candidatas[0] if candidatas else os.path.join(base, nombre)
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(raiz, "build", "driver", "salida", nombre)


def driver_registrado():
    """True si el driver de camara virtual esta registrado en Windows (lo hace
    el instalador). Sin registro, Meet/Teams/Zoom no ven la camara."""
    if not ES_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"CLSID\{CLSID_VCAM}\InprocServer32") as k:
            ruta, _ = winreg.QueryValueEx(k, "")
            return bool(ruta) and os.path.exists(ruta)
    except OSError:
        return False


def listar_camaras():
    """Lista de (indice, nombre) en el mismo orden que usa OpenCV."""
    try:
        from cv2_enumerate_cameras import enumerate_cameras
        cams = [(c.index, (c.name or f"Cámara {c.index + 1}").replace("�", "").strip())
                for c in enumerate_cameras(backend_captura())]
        if cams:
            return cams
    except Exception as e:           # sin permisos, backend ausente, etc.
        log(f"listar_camaras: {e!r}")
    # Respaldo: probar los primeros indices sin nombre.
    import cv2
    cams = []
    for i in range(4):
        cap = cv2.VideoCapture(i, backend_captura())
        if cap.isOpened():
            cams.append((i, f"Cámara {i + 1}"))
        cap.release()
    return cams


def elegir_camara(nombre_guardado):
    """(indice, nombre) de la camara a usar. Nunca elige una virtual por su
    cuenta: leer la propia camara virtual realimentaria la imagen."""
    cams = listar_camaras()
    if nombre_guardado:
        for i, n in cams:
            if n == nombre_guardado:
                return i, n
    for i, n in cams:
        if not es_virtual(n):
            return i, n
    return (None, None)


# ------------------------------------------------------ puente

def comando_puente():
    """Comando que arranca el puente hacia la camara virtual."""
    if EMPAQUETADO:
        exe = "puente-vcam.exe" if ES_WIN else "puente-vcam"
        return [os.path.join(os.path.dirname(sys.executable), exe)]
    py = sys.executable
    if ES_WIN and py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"   # el puente necesita consola para la tuberia
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [py, os.path.join(raiz, "lanzar_puente.py")]


# ------------------------------------------------------ arranque con el sistema

_CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_PLIST = os.path.expanduser("~/Library/LaunchAgents/do.martec.camara.plist")


def _comando_app_oculta():
    if EMPAQUETADO:
        return [sys.executable, "--oculto"]
    py = sys.executable
    if ES_WIN and py.lower().endswith("python.exe"):
        py = py[:-len("python.exe")] + "pythonw.exe"
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [py, os.path.join(raiz, "lanzar_app.py"), "--oculto"]


def autostart_activo():
    if ES_WIN:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN) as k:
                winreg.QueryValueEx(k, APP)
                return True
        except OSError:
            return False
    if ES_MAC:
        return os.path.exists(_PLIST)
    return False


def poner_autostart(activo):
    cmd = _comando_app_oculta()
    if ES_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0,
                            winreg.KEY_SET_VALUE) as k:
            if activo:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ,
                                  " ".join(f'"{c}"' if " " in c else c for c in cmd))
            else:
                try:
                    winreg.DeleteValue(k, APP)
                except OSError:
                    pass
    elif ES_MAC:
        if activo:
            os.makedirs(os.path.dirname(_PLIST), exist_ok=True)
            with open(_PLIST, "wb") as fh:
                plistlib.dump({"Label": "do.martec.camara",
                               "ProgramArguments": cmd,
                               "RunAtLoad": True}, fh)
        elif os.path.exists(_PLIST):
            os.remove(_PLIST)
    log(f"arranque con el sistema -> {'si' if activo else 'no'}")


def aviso_ubicacion_mac():
    """En Mac, una app abierta desde Descargas corre en una ruta temporal
    (App Translocation) y el arranque automatico apuntaria a una ruta que
    desaparece. Devuelve un aviso, o None si la ubicacion sirve."""
    if not (ES_MAC and EMPAQUETADO):
        return None
    ruta = sys.executable
    if "/AppTranslocation/" in ruta or not ruta.startswith("/Applications/"):
        return ("Mueve Martec Cámara a la carpeta Aplicaciones y ábrela desde ahí "
                "para que el arranque automático funcione.")
    return None


def mostrar_icono_bandeja():
    """Windows 11 esconde los iconos nuevos de la bandeja tras la flecha "^".
    Esto lo deja visible en la barra, como una app abierta, pero SOLO si el
    usuario nunca eligio (sin valor IsPromoted): si despues lo esconde a mano,
    se respeta. Devuelve True si lo cambio."""
    if not ES_WIN:
        return False
    import winreg
    propio = {os.path.normcase(os.path.abspath(p)) for p in
              (sys.executable, getattr(sys, "_base_executable", sys.executable)) if p}
    # Windows guarda las rutas de carpetas conocidas con su GUID:
    # "{6D809377-...}\Martec Camara\MartecCamara.exe" = C:\Program Files\...
    carpetas = {}

    def ruta_real(ruta):
        if not (ruta.startswith("{") and "}\\" in ruta):
            return ruta
        guid, resto = ruta[:ruta.index("}") + 1], ruta[ruta.index("}") + 2:]
        if guid not in carpetas:
            carpetas[guid] = _carpeta_conocida(guid)
        return os.path.join(carpetas[guid], resto) if carpetas[guid] else ruta
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as base:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base, i)
                except OSError:
                    return False
                i += 1
                with winreg.OpenKey(base, sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                    try:
                        ruta, _ = winreg.QueryValueEx(k, "ExecutablePath")
                    except OSError:
                        continue
                    if os.path.normcase(os.path.abspath(ruta_real(ruta))) not in propio:
                        continue
                    try:
                        winreg.QueryValueEx(k, "IsPromoted")
                        return False               # el usuario ya eligio: no tocar
                    except OSError:
                        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        log("bandeja: icono visible en la barra de tareas")
                        return True
    except OSError:
        return False


def _carpeta_conocida(guid):
    """Ruta de una carpeta conocida de Windows a partir de su GUID, o None."""
    import ctypes
    import uuid
    ole32 = ctypes.WinDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    try:
        g = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    except ValueError:
        return None
    ruta = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ruta)) != 0:
        return None
    try:
        return ruta.value
    finally:
        ole32.CoTaskMemFree(ruta)


def abrir_url(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def abrir_carpeta_datos():
    d = dir_datos()
    try:
        if ES_WIN:
            os.startfile(d)
        elif ES_MAC:
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])
    except Exception:
        pass
