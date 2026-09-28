"""Icono en la bandeja de Windows (junto al reloj).

Hecho directo con la API de Windows (Shell_NotifyIcon) por ctypes, sin
librerias extra. Corre en su propio hilo con su propio bucle de mensajes y le
pasa las acciones a la ventana (Tk) por una cola, porque Tk no se puede tocar
desde otro hilo.

Acciones que pone en la cola: "mostrar", "encendido", "salir".
"""

import ctypes
import ctypes.wintypes as w
import queue
import threading

from . import sistema

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_COMMAND = 0x0002, 0x0010, 0x0000, 0x0111
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_APP = 0x8000
WM_BANDEJA = WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_DEFAULT = 0x0, 0x800, 0x8, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512

ID_ABRIR, ID_ENCENDIDO, ID_SALIR = 1, 2, 3


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
                ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
                ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]


user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = w.ATOM
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND,
                                   w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
user32.LoadImageW.restype = w.HANDLE
user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPVOID]
user32.LoadIconW.restype = w.HICON
user32.CreatePopupMenu.restype = w.HMENU   # sin esto el handle se trunca en 64 bits
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, w.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user32.DestroyWindow.argtypes = [w.HWND]
user32.DestroyMenu.argtypes = [w.HMENU]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
kernel32.GetModuleHandleW.restype = w.HMODULE


class Bandeja:
    """Icono de Martec Camara en la bandeja de Windows."""

    def __init__(self, esta_encendido):
        # esta_encendido: funcion que devuelve True/False (para la marca del menu).
        self.acciones = queue.Queue()
        self._esta_encendido = esta_encendido
        self._hwnd = None
        self._msg_barra = None
        self._listo = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True, name="bandeja")

    def iniciar(self):
        self._hilo.start()
        self._listo.wait(3)

    def avisar(self, titulo, texto):
        """Globo de aviso junto al icono."""
        if not self._hwnd:
            return
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = titulo[:63]
        nid.szInfo = texto[:255]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def cerrar(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # --- hilo de la bandeja
    def _nid(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _agregar_icono(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_BANDEJA
        nid.hIcon = self._icono
        nid.szTip = "Martec Cámara"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING | MF_DEFAULT, ID_ABRIR, "Abrir Martec Cámara")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        marca = MF_CHECKED if self._esta_encendido() else 0
        user32.AppendMenuW(menu, MF_STRING | marca, ID_ENCENDIDO, "Encendido")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_SALIR, "Salir")
        pt = w.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Sin SetForegroundWindow el menu no se cierra al hacer clic afuera
        # (comportamiento documentado de Windows).
        user32.SetForegroundWindow(self._hwnd)
        eleccion = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                         pt.x, pt.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        return eleccion

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_BANDEJA:
            evento = lparam & 0xFFFF
            if evento in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.acciones.put("mostrar")
            elif evento == WM_RBUTTONUP:
                eleccion = self._menu()
                if eleccion == ID_ABRIR:
                    self.acciones.put("mostrar")
                elif eleccion == ID_ENCENDIDO:
                    self.acciones.put("encendido")
                elif eleccion == ID_SALIR:
                    self.acciones.put("salir")
            return 0
        if msg == self._msg_barra:
            self._agregar_icono()      # el Explorador se reinicio: volver a poner el icono
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _correr(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._wndproc = WNDPROC(self._proc)       # guardar la referencia: si no, se libera
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "MartecCamaraBandeja"
            user32.RegisterClassExW(ctypes.byref(wc))
            # Antes de crear la ventana: CreateWindowExW ya le manda mensajes a _proc.
            self._msg_barra = user32.RegisterWindowMessageW("TaskbarCreated")
            self._hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "Martec Cámara", 0,
                                                0, 0, 0, 0, None, None, hinst, None)
            self._icono = user32.LoadImageW(None, sistema.ruta_recurso("icono.ico"), IMAGE_ICON,
                                            0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not self._icono:
                self._icono = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
            self._agregar_icono()
        except Exception as e:
            sistema.log(f"bandeja: no se pudo crear el icono ({e!r})")
            self._hwnd = None
            self._listo.set()
            return
        self._listo.set()
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None

    @property
    def activa(self):
        return bool(self._hwnd)
