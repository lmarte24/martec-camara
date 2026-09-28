# Receta de PyInstaller para Martec Camara (Windows y macOS).
#
# Windows: un programa, MartecCamara.exe (MIT). La camara virtual es el driver
#   propio "Martec Camara" (build/driver), que instala build/instalador.iss.
# macOS: dos programas que comparten librerias:
#   MartecCamara   la app (MIT)
#   puente-vcam    publica en OBS Virtual Camera. GPL-2.0 porque usa
#                  pyvirtualcam; va aparte para no mezclar licencias.
#
# Uso:   pyinstaller --noconfirm build/martec_camara.spec
# Salida: dist/MartecCamara/  (Windows)   o   dist/MartecCamara.app  (macOS)

import os
import sys

ES_MAC = sys.platform == "darwin"
RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(RAIZ, "src")
VERSION = "0.1.0"

# Fuera lo que no se usa, para que el paquete pese menos:
#  - el plugin FFmpeg de OpenCV solo sirve para archivos y streams de video;
#    la webcam se captura por DirectShow / AVFoundation. Ademas asi no se
#    distribuye FFmpeg (LGPL).
#  - libcrypto / libssl: la app no abre conexiones cifradas.
SOBRAN = ("opencv_videoio_ffmpeg", "libcrypto", "libssl")
EXCLUIR = ["matplotlib", "PIL", "pytest", "ssl", "_ssl", "_hashlib"]


def sin_sobrantes(binarios):
    return [b for b in binarios
            if not any(s in os.path.basename(b[0]).lower() for s in SOBRAN)]


app_a = Analysis(
    [os.path.join(SRC, "lanzar_app.py")],
    pathex=[SRC],
    datas=[(os.path.join(SRC, "martec_camara", "recursos"), "recursos")],
    hiddenimports=(["cv2_enumerate_cameras.macos_backend"] if ES_MAC else
                   ["cv2_enumerate_cameras.windows_backend"]),
    excludes=["pyvirtualcam", "puente_vcam"] + EXCLUIR,
    noarchive=False,
)
app_a.binaries = sin_sobrantes(app_a.binaries)
app_exe = EXE(
    PYZ(app_a.pure), app_a.scripts, [],
    exclude_binaries=True,
    name="MartecCamara",
    console=False,                  # app de ventana, sin consola
    upx=False,
    icon=os.path.join(RAIZ, "build", "icono.ico") if not ES_MAC and
         os.path.exists(os.path.join(RAIZ, "build", "icono.ico")) else None,
)
partes = [app_exe, app_a.binaries, app_a.datas]

if ES_MAC:
    puente_a = Analysis(
        [os.path.join(SRC, "lanzar_puente.py")],
        pathex=[SRC],
        hiddenimports=["pyvirtualcam._native_macos_obs_cmioextension",
                       "pyvirtualcam._native_macos_obs_dal"],
        excludes=["cv2", "tkinter", "martec_camara", "cv2_enumerate_cameras"] + EXCLUIR,
        noarchive=False,
    )
    puente_a.binaries = sin_sobrantes(puente_a.binaries)
    puente_exe = EXE(
        PYZ(puente_a.pure), puente_a.scripts, [],
        exclude_binaries=True,
        name="puente-vcam",
        console=True,               # tuberia de entrada/salida fiable
        upx=False,
    )
    partes += [puente_exe, puente_a.binaries, puente_a.datas]

coll = COLLECT(*partes, name="MartecCamara", upx=False)

if ES_MAC:
    icns = os.path.join(RAIZ, "build", "icono.icns")
    app = BUNDLE(
        coll,
        name="MartecCamara.app",
        icon=icns if os.path.exists(icns) else None,
        bundle_identifier="do.martec.camara",
        version=VERSION,
        info_plist={
            "CFBundleName": "Martec Cámara",
            "CFBundleDisplayName": "Martec Cámara",
            "CFBundleShortVersionString": VERSION,
            # Sin esta clave macOS cierra la app al pedir la camara.
            "NSCameraUsageDescription":
                "Martec Cámara usa tu cámara para seguir tu cara, aplicar el zoom "
                "y el retoque, y enviar la imagen a la cámara virtual.",
            # numpy 2.5 para Apple Silicon exige macOS 14.
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
        },
    )
