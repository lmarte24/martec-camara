"""Ventana de Martec Camara.

Hecha con Tk, que viene con Python, para no sumar dependencias. Solo muestra
el estado del motor y le pasa los ajustes; todo el trabajo de imagen lo hace
el motor en su propio hilo.
"""

import argparse
import faulthandler
import os
import sys
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from . import sistema
from .motor import AIRE_MAX, AIRE_MIN, ENCUADRE_MAX, ENCUADRE_MIN, Motor

VERDE, AMBAR, ROJO, GRIS = "#15803d", "#b45309", "#b91c1c", "#6b7280"

VCAM = sistema.nombre_camara_visible() if sistema.ES_WIN else "OBS Virtual Camera"

ESTADOS = {
    "iniciando": ("Iniciando la cámara…", GRIS),
    "transmitiendo": ("Funcionando: una app está usando la cámara", VERDE),
    "vista_previa": ("Lista", VERDE),
    "en_espera": ("Lista, en espera", VERDE),
    "sin_driver": ("Falta el driver de la cámara virtual", ROJO),
    "sin_obs": ("Falta la cámara virtual de OBS", AMBAR),
    "ocupada": ("Otra aplicación está usando tu cámara", AMBAR),
    "oscura": ("La imagen está muy oscura", AMBAR),
    "sin_camara": ("No se encontró la cámara", ROJO),
    "sin_imagen": ("La cámara no entrega imagen", ROJO),
    "apagado": ("Apagado", GRIS),
    "error": ("Error", ROJO),
}


def explicacion(estado, detalle, camara):
    if estado == "transmitiendo":
        return (f"La imagen con encuadre y retoque está saliendo por «{VCAM}». "
                f"Tu cámara real es «{camara}».")
    if estado == "vista_previa":
        return (f"En Meet, Teams o Zoom elige la cámara «{VCAM}». "
                "Ninguna app la está usando todavía.")
    if estado == "en_espera":
        return (f"En Meet, Teams o Zoom elige la cámara «{VCAM}»: tu webcam se "
                "enciende sola cuando una app la abre. Mientras tanto queda libre.")
    if estado == "sin_driver":
        return ("Las apps de video no pueden ver «Martec Cámara». Vuelve a ejecutar "
                "el instalador de Martec Cámara para reparar el driver."
                + (f"\n\nDetalle: {detalle}" if detalle else ""))
    if estado == "sin_obs":
        if sistema.ES_MAC:
            pasos = ("Instala OBS Studio (es gratis), ábrelo una vez, acepta la "
                     "extensión de cámara que pide macOS y ciérralo.")
        else:
            pasos = ("Instala OBS Studio (es gratis). Solo hay que instalarlo: "
                     "no hace falta abrirlo.")
        extra = ("Si OBS está abierto con su cámara virtual encendida, apágala: "
                 "solo un programa a la vez puede usarla.")
        return f"{pasos}\n\n{extra}" + (f"\n\nDetalle: {detalle}" if detalle else "")
    if estado == "ocupada":
        return (f"Otra aplicación tiene tomada tu cámara «{camara}». En esa aplicación "
                f"elige «{VCAM}» en lugar de tu cámara, o ciérrala. "
                "Esta app vuelve a intentar sola.")
    if estado == "oscura":
        return (f"Tu cámara «{camara}» funciona, pero ve casi todo negro. Enciende la "
                "luz o revisa que nada tape el lente. Se sigue transmitiendo.")
    if estado == "sin_camara":
        return "Conecta una cámara o elige otra en la lista."
    if estado == "sin_imagen":
        return "Desconecta la cámara y vuelve a conectarla, o elige otra en la lista."
    if estado == "apagado":
        return "Tu cámara real queda libre para usarla directamente en otras aplicaciones."
    if estado == "error":
        return detalle or "Error inesperado. Revisa el registro."
    return ""


def a_ppm(rgb):
    h, w = rgb.shape[:2]
    return b"P6\n%d %d\n255\n" % (w, h) + rgb.tobytes()


class App:

    def __init__(self, root, oculto):
        self.root = root
        self.aj = sistema.leer_ajustes()
        self.motor = Motor(self.aj)
        # Arranque con Windows (oculta): sin ventana no hay vista previa, asi que
        # la webcam no se enciende hasta que una app use la camara virtual.
        self.motor.vista_activa = not oculto
        self.motor.start()
        self._guardar_pendiente = None
        self._arranque_oculto = oculto
        self._ultimo_estado = None
        self._camaras = []

        # Icono en la bandeja (Windows): la X oculta la ventana ahi.
        self.bandeja = None
        if sistema.ES_WIN:
            from .bandeja import Bandeja
            self.bandeja = Bandeja(lambda: bool(self.aj.get("encendido")))
            self.bandeja.iniciar()
            if not self.bandeja.activa:
                self.bandeja = None

        root.title(f"{sistema.NOMBRE} {sistema.VERSION}")
        root.resizable(False, False)
        try:
            self._icono = tk.PhotoImage(file=sistema.ruta_recurso("icono.png"))
            root.iconphoto(True, self._icono)
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self.minimizar)
        if oculto and self.bandeja:
            root.withdraw()      # directo a la bandeja, antes del primer refresco
        self._armar()
        self._cargar_camaras()
        self._tick()
        self._tick_lento()
        self._tick_bandeja()
        if self.bandeja:
            # El Explorador registra el icono unos segundos despues de crearlo.
            root.after(4000, sistema.mostrar_icono_bandeja)
        if oculto and not self.bandeja:
            # Sin bandeja (Mac): minimizada. Iconify necesita la ventana ya creada;
            # mientras tanto no cuenta como visible.
            root.after(300, self._minimizar_al_arrancar)

    # ------------------------------------------------------------ interfaz
    def _armar(self):
        est = ttk.Style()
        if "clam" in est.theme_names():
            est.theme_use("clam")
        base = ttk.Frame(self.root, padding=12)
        base.grid(sticky="nsew")

        # Izquierda: vista previa.
        izq = ttk.Frame(base)
        izq.grid(row=0, column=0, sticky="n")
        self._vacia = tk.PhotoImage(width=480, height=270)
        self.lbl_vista = tk.Label(izq, image=self._vacia, bg="#111", bd=0)
        self.lbl_vista.grid(row=0, column=0, columnspan=2)
        self.foto = None
        self.var_vista = tk.StringVar(value="salida")
        ttk.Radiobutton(izq, text="Lo que ven los demás", value="salida",
                        variable=self.var_vista).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Radiobutton(izq, text="Cámara completa", value="original",
                        variable=self.var_vista).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.lbl_fps = ttk.Label(izq, text="", foreground=GRIS)
        self.lbl_fps.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Derecha: estado y controles.
        der = ttk.Frame(base, width=330)
        der.grid(row=0, column=1, sticky="n", padx=(16, 0))
        self.lbl_estado = tk.Label(der, text="", font=("TkDefaultFont", 12, "bold"),
                                   anchor="w", justify="left")
        self.lbl_estado.grid(row=0, column=0, columnspan=2, sticky="we")
        self.lbl_explica = ttk.Label(der, text="", wraplength=320, justify="left")
        self.lbl_explica.grid(row=1, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.btn_obs = ttk.Button(der, text="Descargar OBS Studio",
                                  command=lambda: sistema.abrir_url(sistema.URL_OBS))
        self.btn_obs.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.btn_obs.grid_remove()

        ttk.Separator(der).grid(row=3, column=0, columnspan=2, sticky="we", pady=10)

        ttk.Label(der, text="Cámara").grid(row=4, column=0, columnspan=2, sticky="w")
        self.cmb_cam = ttk.Combobox(der, state="readonly", width=34)
        self.cmb_cam.grid(row=5, column=0, sticky="we")
        self.cmb_cam.bind("<<ComboboxSelected>>", self._elegir_camara)
        ttk.Button(der, text="↻", width=3, command=self._cargar_camaras).grid(
            row=5, column=1, padx=(4, 0))

        self.var_on = tk.BooleanVar(value=self.aj["encendido"])
        ttk.Checkbutton(der, text="Encendido", variable=self.var_on,
                        command=lambda: self._cambio(encendido=self.var_on.get())
                        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.var_seg = tk.BooleanVar(value=self.aj["seguimiento"])
        ttk.Checkbutton(der, text="Seguir mi cara con zoom", variable=self.var_seg,
                        command=lambda: self._cambio(seguimiento=self.var_seg.get())
                        ).grid(row=7, column=0, columnspan=2, sticky="w")

        rango_enc = ENCUADRE_MAX - ENCUADRE_MIN
        self._escala(der, 8, "Acercamiento",
                     (ENCUADRE_MAX - self.aj["encuadre"]) / rango_enc * 100,
                     lambda v: self._cambio(encuadre=round(ENCUADRE_MAX - v / 100 * rango_enc, 3)))
        rango_aire = AIRE_MAX - AIRE_MIN
        self._escala(der, 10, "Espacio sobre la cabeza",
                     (self.aj["aire"] - AIRE_MIN) / rango_aire * 100,
                     lambda v: self._cambio(aire=round(AIRE_MIN + v / 100 * rango_aire, 3)))
        self._escala(der, 12, "Retoque de piel", self.aj["piel"] * 100,
                     lambda v: self._cambio(piel=round(v / 100, 3)))

        self.var_esp = tk.BooleanVar(value=self.aj["espejo"])
        ttk.Checkbutton(der, text="Espejo", variable=self.var_esp,
                        command=lambda: self._cambio(espejo=self.var_esp.get())
                        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.var_auto = tk.BooleanVar(value=sistema.autostart_activo())
        ttk.Checkbutton(der, text="Iniciar con el sistema", variable=self.var_auto,
                        command=self._autostart).grid(row=15, column=0, columnspan=2, sticky="w")
        aviso = sistema.aviso_ubicacion_mac()
        if aviso:
            ttk.Label(der, text=aviso, wraplength=320, foreground=AMBAR).grid(
                row=16, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Separator(der).grid(row=17, column=0, columnspan=2, sticky="we", pady=10)
        ttk.Label(der, wraplength=320, foreground=GRIS,
                  text=("Cerrar la ventana la deja en la bandeja, junto al reloj (o en la "
                        "flecha ^): la cámara sigue funcionando. Para cerrar del todo usa «Salir»."
                        if sistema.ES_WIN else
                        "Cerrar la ventana solo la minimiza: la cámara sigue funcionando. "
                        "Para cerrar del todo usa «Salir».")).grid(
            row=18, column=0, columnspan=2, sticky="w")
        fila = ttk.Frame(der)
        fila.grid(row=19, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(fila, text="Registros", command=sistema.abrir_carpeta_datos).pack(side="left")
        ttk.Button(fila, text="Salir", command=self.salir).pack(side="right")

    def _escala(self, padre, fila, texto, valor, al_cambiar):
        ttk.Label(padre, text=texto).grid(row=fila, column=0, columnspan=2,
                                          sticky="w", pady=(8, 0))
        esc = ttk.Scale(padre, from_=0, to=100, orient="horizontal", length=320,
                        command=lambda v: al_cambiar(float(v)))
        esc.set(max(0.0, min(100.0, valor)))
        esc.grid(row=fila + 1, column=0, columnspan=2, sticky="we")

    # ------------------------------------------------------------ acciones
    def _cambio(self, **kw):
        self.aj.update(kw)
        self.motor.actualizar(**kw)
        if self._guardar_pendiente:
            self.root.after_cancel(self._guardar_pendiente)
        self._guardar_pendiente = self.root.after(
            600, lambda: sistema.guardar_ajustes(self.aj))

    def _cargar_camaras(self):
        self._camaras = sistema.listar_camaras()
        etiquetas = [n + ("  (virtual)" if sistema.es_virtual(n) else "")
                     for _, n in self._camaras]
        self.cmb_cam["values"] = etiquetas
        actual = self.aj.get("camara") or self.motor.camara
        for pos, (_, n) in enumerate(self._camaras):
            if n == actual:
                self.cmb_cam.current(pos)
                break
        else:
            for pos, (_, n) in enumerate(self._camaras):
                if not sistema.es_virtual(n):
                    self.cmb_cam.current(pos)
                    break

    def _elegir_camara(self, _evento=None):
        pos = self.cmb_cam.current()
        if pos < 0 or pos >= len(self._camaras):
            return
        nombre = self._camaras[pos][1]
        if sistema.es_virtual(nombre):
            messagebox.showwarning(
                sistema.NOMBRE,
                "Esa es una cámara virtual. Elige tu cámara real: la cámara virtual "
                "es la que esta app publica para Meet, Teams o Zoom.")
            self._cargar_camaras()
            return
        self._cambio(camara=nombre)

    def _autostart(self):
        try:
            sistema.poner_autostart(self.var_auto.get())
        except OSError as e:
            messagebox.showerror(sistema.NOMBRE, f"No se pudo cambiar el arranque: {e}")
            self.var_auto.set(sistema.autostart_activo())

    def minimizar(self):
        """La X: a la bandeja si existe; si no, minimizar."""
        if not self.bandeja:
            self.root.iconify()
            return
        self.root.withdraw()
        if not self.aj.get("aviso_bandeja"):
            self.bandeja.avisar(sistema.NOMBRE,
                                "Sigue funcionando aquí. Clic para abrirla; "
                                "clic derecho > Salir para cerrarla del todo. "
                                "Si no ves el ícono, está en la flecha ^ junto al reloj: "
                                "arrástralo a la barra para dejarlo siempre a la vista.")
            self._cambio(aviso_bandeja=True)

    def _tick_bandeja(self):
        if self.bandeja:
            while True:
                try:
                    accion = self.bandeja.acciones.get_nowait()
                except Exception:
                    break
                if accion == "mostrar":
                    self.mostrar()
                elif accion == "encendido":
                    self.var_on.set(not self.var_on.get())
                    self._cambio(encendido=self.var_on.get())
                elif accion == "salir":
                    self.salir()
                    return
        self.root.after(150, self._tick_bandeja)

    def _minimizar_al_arrancar(self):
        self.root.iconify()
        self._arranque_oculto = False

    def mostrar(self):
        self._arranque_oculto = False
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def salir(self):
        sistema.guardar_ajustes(self.aj)
        if self.bandeja:
            self.bandeja.cerrar()
        self.motor.detener()
        self.motor.join(timeout=4)
        sistema.log("=== salida ===")
        self.root.destroy()

    # ------------------------------------------------------------ refresco
    def _tick(self):
        # Si la ventana no esta a la vista, el motor puede soltar la webcam
        # cuando ninguna app use la camara virtual.
        visible = (self.root.state() not in ("iconic", "withdrawn")
                   and not self._arranque_oculto)
        self.motor.vista_activa = visible
        rgb = (self.motor.vista_salida if self.var_vista.get() == "salida"
               else self.motor.vista_original)
        if rgb is not None and visible:
            try:
                datos = a_ppm(rgb)
                if self.foto is None or self.foto.width() != rgb.shape[1]:
                    self.foto = tk.PhotoImage(data=datos, format="PPM")
                    self.lbl_vista.configure(image=self.foto)
                else:
                    self.foto.configure(data=datos, format="PPM")
            except tk.TclError as e:
                sistema.log(f"vista previa: {e}")
        elif rgb is None and self.foto is not None:
            self.foto = None
            self.lbl_vista.configure(image=self._vacia)

        clave = (self.motor.estado, self.motor.detalle, self.motor.camara)
        if clave != self._ultimo_estado:
            self._ultimo_estado = clave
            titulo, color = ESTADOS.get(self.motor.estado, (self.motor.estado, GRIS))
            self.lbl_estado.configure(text=titulo, fg=color)
            self.lbl_explica.configure(text=explicacion(*clave))
            if self.motor.estado == "sin_obs" and not sistema.ES_WIN:
                self.btn_obs.grid()
            else:
                self.btn_obs.grid_remove()
        self.root.after(66, self._tick)

    def _tick_lento(self):
        if sistema.hay_pedido_mostrar():
            self.mostrar()
        if self.motor.estado in ("transmitiendo", "vista_previa", "sin_obs",
                                 "sin_driver", "oscura"):
            cara = "cara detectada" if self.motor.hay_cara else "buscando cara"
            app = "  ·  app conectada" if self.motor.app_conectada else ""
            self.lbl_fps.configure(text=f"{self.motor.fps:.0f} cuadros/s  ·  {cara}{app}")
        elif self.motor.estado == "en_espera":
            self.lbl_fps.configure(text="webcam apagada: ninguna app la está usando")
        else:
            self.lbl_fps.configure(text="")
        self.root.after(1000, self._tick_lento)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="MartecCamara")
    ap.add_argument("--oculto", action="store_true",
                    help="arrancar minimizada (lo usa el inicio con el sistema)")
    ap.add_argument("--diagnostico", action="store_true",
                    help="probar las camaras, anotar el resultado en el registro y salir")
    args = ap.parse_args(argv)
    if args.diagnostico:
        return diagnostico()

    candado = sistema.Candado()
    if not candado.tomar():
        sistema.pedir_mostrar()        # ya esta abierta: que muestre su ventana
        return 0
    _registrar_errores()
    sistema.log(f"=== inicio {sistema.NOMBRE} {sistema.VERSION} "
                f"({'empaquetada' if sistema.EMPAQUETADO else 'desarrollo'}) ===")

    if sistema.ES_WIN:
        try:                            # nitidez en pantallas con escala
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.report_callback_exception = lambda t, v, tb: sistema.log(
        "ventana: " + "".join(traceback.format_exception(t, v, tb)))
    App(root, args.oculto)
    root.mainloop()
    return 0


def diagnostico():
    """Prueba cada camara con cada metodo de captura y lo anota en el
    registro. Pensado para que un usuario pueda mandar el resultado."""
    import cv2
    sistema.log(f"=== diagnostico {sistema.VERSION} OpenCV {cv2.__version__} ===")
    try:
        nombres = [cv2.videoio_registry.getBackendName(b)
                   for b in cv2.videoio_registry.getCameraBackends()]
        sistema.log(f"backends de camara: {nombres}")
    except Exception as e:
        sistema.log(f"backends de camara: error {e!r}")
    sistema.log(f"camaras listadas: {sistema.listar_camaras()}")
    metodos = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("ANY", cv2.CAP_ANY)] \
        if sistema.ES_WIN else [("AVFOUNDATION", cv2.CAP_AVFOUNDATION), ("ANY", cv2.CAP_ANY)]
    for idx in range(3):
        for nombre, api in metodos:
            cap = cv2.VideoCapture(idx, api)
            abre = cap.isOpened()
            ok, f = cap.read() if abre else (False, None)
            cap.release()
            sistema.log(f"  indice {idx} {nombre:12} abre={abre} lee={ok} "
                        f"{'' if f is None else f.shape}")
    sistema.log("=== fin diagnostico ===")
    return 0


def _registrar_errores():
    """En la app empaquetada no hay consola: sin esto, un error en un hilo se
    pierde en silencio. Con MARTEC_DEBUG=1 ademas se vuelca a los 20 s donde
    esta parado cada hilo (sirve para diagnosticar cuelgues)."""
    threading.excepthook = lambda a: sistema.log(
        f"hilo {a.thread.name if a.thread else '?'}: " + "".join(
            traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    sys.excepthook = lambda t, v, tb: sistema.log(
        "".join(traceback.format_exception(t, v, tb)))
    if os.environ.get("MARTEC_DEBUG"):
        fh = open(os.path.join(sistema.dir_datos(), "volcado.txt"), "w")
        faulthandler.dump_traceback_later(20, repeat=False, file=fh)
        _registrar_errores.fh = fh     # que no se cierre


if __name__ == "__main__":
    sys.exit(main())
