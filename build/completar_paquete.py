"""Completa la carpeta que se reparte: LEEME, licencias y fuentes GPL.

Uso:  python build/completar_paquete.py <carpeta-destino>

- licencias/  textos de licencia de cada componente incluido.
- fuentes/    codigo del puente (GPL-2.0) y el paquete fuente de pyvirtualcam
              (GPL-2.0): la GPL exige acompanar el binario con su fuente.
- LEEME.txt   instrucciones cortas para el usuario.
"""

import importlib.metadata as md
import os
import shutil
import sys
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
ES_WIN = sys.platform == "win32"

PAQUETES = ["opencv-python-headless", "numpy", "pyvirtualcam", "cv2_enumerate_cameras",
            "pyinstaller", "pyobjc-core", "pyobjc-framework-AVFoundation"]


def copiar_licencias_paquete(nombre, dest):
    try:
        dist = md.distribution(nombre)
    except md.PackageNotFoundError:
        return 0
    n = 0
    for f in dist.files or []:
        base = os.path.basename(str(f)).lower()
        # .xml fuera: OpenCV trae un clasificador de matriculas ("license
        # plate") que no es una licencia.
        if base.endswith(".xml"):
            continue
        if any(k in base for k in ("license", "licence", "copying", "notice")):
            origen = f.locate()
            if os.path.isfile(origen):
                # Nombre con la ruta completa: numpy tiene varios LICENSE.txt en
                # subcarpetas distintas y con solo el nombre se pisaban.
                plano = str(f).replace("/", "_").replace("\\", "_")
                shutil.copy(origen, os.path.join(dest, f"{dist.metadata['Name']}-{plano}"))
                n += 1
    return n


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    destino = os.path.abspath(sys.argv[1])
    lic = os.path.join(destino, "licencias")
    fue = os.path.join(destino, "fuentes")
    # Empezar de cero: una corrida anterior pudo dejar archivos que ya no van.
    shutil.rmtree(lic, ignore_errors=True)
    shutil.rmtree(fue, ignore_errors=True)
    os.makedirs(lic, exist_ok=True)
    os.makedirs(fue, exist_ok=True)

    # Licencia propia y avisos de terceros.
    shutil.copy(os.path.join(RAIZ, "LICENSE"), os.path.join(lic, "MartecCamara-LICENSE.txt"))
    shutil.copy(os.path.join(RAIZ, "THIRD_PARTY_NOTICES.md"), os.path.join(lic, "AVISOS-DE-TERCEROS.md"))
    # Detector de caras YuNet (MIT, Shiqi Yu), en Windows y en Mac.
    shutil.copy(os.path.join(RAIZ, "third_party", "yunet", "LICENSE"),
                os.path.join(lic, "YuNet-LICENSE.txt"))
    if ES_WIN:
        # Driver propio: softcam (MIT) con las BaseClasses de DirectShow (MIT).
        shutil.copy(os.path.join(RAIZ, "third_party", "softcam", "LICENSE"),
                    os.path.join(lic, "softcam-LICENSE.txt"))
        # Driver moderno de Windows 11: derivado de VCamSample (MIT).
        shutil.copy(os.path.join(RAIZ, "driver_mf", "LICENSE-VCamSample.txt"),
                    os.path.join(lic, "VCamSample-LICENSE.txt"))
    else:
        shutil.copy(os.path.join(RAIZ, "src", "puente_vcam", "LICENSE"),
                    os.path.join(lic, "puente-vcam-LICENSE-GPL-2.0.txt"))

    for p in PAQUETES:
        if ES_WIN and p in ("pyvirtualcam", "pyobjc-core", "pyobjc-framework-AVFoundation"):
            continue                  # no van en el paquete de Windows
        n = copiar_licencias_paquete(p, lic)
        print(f"  licencias de {p}: {n}")

    # Python y Tcl/Tk.
    for rel, nombre in (("LICENSE.txt", "Python-LICENSE.txt"),
                        ("LICENSE", "Python-LICENSE.txt"),
                        (os.path.join("tcl", "tcl8.6", "license.terms"), "Tcl-Tk-license.terms"),
                        (os.path.join("tcl", "tk8.6", "license.terms"), "Tcl-Tk-license.terms")):
        ruta = os.path.join(sys.base_prefix, rel)
        if os.path.isfile(ruta):
            shutil.copy(ruta, os.path.join(lic, nombre))

    if ES_WIN:
        # En Windows no hay componentes GPL: no hace falta carpeta de fuentes.
        shutil.rmtree(fue, ignore_errors=True)
        shutil.copy(os.path.join(AQUI, "LEEME-windows.txt"), os.path.join(destino, "LEEME.txt"))
        print(f"paquete completado en {destino}")
        return 0

    # Fuentes GPL (Mac): el puente y pyvirtualcam.
    dest_puente = os.path.join(fue, "puente_vcam")
    os.makedirs(dest_puente, exist_ok=True)
    for f in ("puente.py", "LICENSE"):
        shutil.copy(os.path.join(RAIZ, "src", "puente_vcam", f), dest_puente)
    shutil.copy(os.path.join(RAIZ, "src", "lanzar_puente.py"), dest_puente)
    version = md.version("pyvirtualcam")
    # pyvirtualcam solo publica binarios en PyPI; el fuente se toma de la
    # etiqueta oficial del proyecto en GitHub.
    url = f"https://github.com/letmaik/pyvirtualcam/archive/refs/tags/v{version}.zip"
    dest_zip = os.path.join(fue, f"pyvirtualcam-{version}-fuente.zip")
    try:
        urllib.request.urlretrieve(url, dest_zip)
        print(f"  fuente de pyvirtualcam: {os.path.getsize(dest_zip):,} bytes")
    except Exception as e:
        print(f"AVISO: no se pudo bajar {url} ({e}); agregarlo a mano en fuentes/")
        return 1
    with open(os.path.join(fue, "LEEME-FUENTES.txt"), "w", encoding="utf-8") as fh:
        fh.write(
            "Fuentes de los componentes GPL-2.0 incluidos en este paquete.\n\n"
            "puente_vcam/          codigo del programa puente-vcam (GPL-2.0).\n"
            f"pyvirtualcam-{version}-fuente.zip  codigo fuente de pyvirtualcam (GPL-2.0),\n"
            f"                      etiqueta v{version} de https://github.com/letmaik/pyvirtualcam\n\n"
            "El resto de Martec Camara es un programa aparte con licencia MIT.\n"
            "Codigo completo del proyecto: ver README en el repositorio.\n")

    shutil.copy(os.path.join(AQUI, "LEEME-mac.txt"), os.path.join(destino, "LEEME.txt"))
    print(f"paquete completado en {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
