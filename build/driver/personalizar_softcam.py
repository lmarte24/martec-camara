"""Copia softcam (third_party/softcam, MIT) y lo personaliza como "Martec Camara".

Cambios, todos necesarios para que conviva con otros programas basados en
softcam y para que el nombre visible sea el de Martec:
  - nombre del dispositivo:  "DirectShow Softcam" -> "Martec Cámara"
  - CLSID propio y FIJO (no cambiarlo nunca: las actualizaciones dependen de el)
  - nombres de la memoria compartida y del mutex propios

Uso:  python build/driver/personalizar_softcam.py <destino>
"""

import os
import re
import shutil
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
ORIGEN = os.path.join(AQUI, "..", "..", "third_party", "softcam")

# {24BE734C-01F4-4D96-83D8-F93DC74CE46C}  --  CLSID de Martec Camara. FIJO.
CLSID_TEXTO = "24BE734C-01F4-4D96-83D8-F93DC74CE46C"
CLSID_DEFINE = ("0x24be734c, 0x01f4, 0x4d96, 0x83, 0xd8, 0xf9, 0x3d, 0xc7, 0x4c, "
                "0xe4, 0x6c")
# á en vez de la letra, para no depender de la codificacion del fuente.
NOMBRE_W = 'L"Martec C\\u00e1mara"'
NOMBRE_A = "Martec Camara"
# Nombre de la memoria compartida y del mutex, con version del protocolo.
# Se cambio a "v2" al pasar a latido por cuadro: asi un driver viejo que siga
# cargado en Chrome, Slack o Claude (con su memoria vieja abierta) no choca con
# el nuevo ("otra copia ya esta usando la camara virtual").
NOMBRE_MEM = "Martec Camara v2"


def reemplazar(ruta, cambios):
    with open(ruta, encoding="utf-8") as fh:
        texto = fh.read()
    for patron, nuevo, esperado in cambios:
        # Texto: va literal (sin interpretar barras invertidas). Funcion: se usa tal cual.
        repl = nuevo if callable(nuevo) else (lambda _m, t=nuevo: t)
        texto, n = re.subn(patron, repl, texto)
        if n != esperado:
            raise SystemExit(f"{os.path.basename(ruta)}: '{patron}' se esperaba "
                             f"{esperado} vez/veces y hubo {n}. softcam cambio: revisar.")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(texto)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    destino = os.path.abspath(sys.argv[1])
    shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(ORIGEN, destino, ignore=shutil.ignore_patterns(".git", "tests", "examples"))

    reemplazar(os.path.join(destino, "src", "softcam", "softcam.cpp"), [
        (r"// \{AEF3B972-5FA5-4647-9571-358EB472BC9E\}",
         f"// {{{CLSID_TEXTO}}}  Martec Camara", 1),
        (r"DEFINE_GUID\(CLSID_DShowSoftcam,\s*0xaef3b972[^)]*\);",
         f"DEFINE_GUID(CLSID_DShowSoftcam,\n{CLSID_DEFINE});", 1),
        (r'const wchar_t FILTER_NAME\[\] = L"DirectShow Softcam";',
         f"const wchar_t FILTER_NAME[] = {NOMBRE_W};", 1),
    ])
    # "Conectado" = una app TOMA CUADROS, no solo abrio el driver.
    # En softcam original el receptor late cada 20 ms desde que abre la memoria
    # compartida, y eso pasa con solo consultar el formato: Chrome, Slack o
    # Claude, que cargan el driver al listar camaras, quedaban "conectados" sin
    # mirar nada, y Martec Camara mantenia la webcam encendida (luz siempre
    # prendida, ciclo de encender/apagar). Ahora el latido lo da cada cuadro
    # entregado, y el emisor tolera 2 s sin cuadros antes de dar por ido al
    # receptor (con la webcam apagada la app manda un cuadro de espera cada 0.5 s).
    latido_hilo = (r"\s*fb\.m_receiver_watchdog = Watchdog::createHeartbeat\(\s*"
                   r"WATCHDOG_HEARTBEAT_INTERVAL,\s*\[mutex, frame\]\(\) mutable\s*\{\s*"
                   r"std::lock_guard<NamedMutex> lock\(mutex\);\s*"
                   r"frame->m_watchdog_receiver_heartbeat \+= 1;\s*\}\);")
    latido_inicial = (r"(frame->m_connected_min_version = ProtocolVersion;\s*\}\s*)"
                      r"frame->m_watchdog_receiver_heartbeat \+= 1;")
    reemplazar(os.path.join(destino, "src", "softcamcore", "FrameBuffer.cpp"), [
        (r'"DirectShow Softcam/NamedMutex"', f'"{NOMBRE_MEM}/NamedMutex"', 1),
        (r'"DirectShow Softcam/SharedMemory"', f'"{NOMBRE_MEM}/SharedMemory"', 1),
        (latido_hilo, "\n        // Martec: sin hilo de latido; late cada cuadro (transferToDIB).", 1),
        (latido_inicial, lambda m: m.group(1) + "// Martec: abrir no cuenta como conexion.", 1),
        (r"(\*out_frame_counter = frame->m_frame_counter;)",
         lambda m: "frame->m_watchdog_receiver_heartbeat += 1; // Martec: late al entregar un cuadro\n        "
                   + m.group(1), 1),
        (r"(fb\.m_receiver_watchdog = Watchdog::createMonitor\(\s*WATCHDOG_MONITOR_INTERVAL,\s*)WATCHDOG_TIMEOUT,",
         lambda m: m.group(1) + "2.0f, // Martec: 2 s sin cuadros = receptor ido", 1),
    ])
    reemplazar(os.path.join(destino, "src", "softcamcore", "DShowSoftcam.cpp"), [
        (r'NAME\("DirectShow Softcam"\)', f'NAME("{NOMBRE_A}")', 1),
        (r'NAME\("DirectShow Softcam Stream"\)', f'NAME("{NOMBRE_A} Stream")', 1),
        (r'L"DirectShow Softcam Stream"', f'L"{NOMBRE_A} Stream"', 1),
    ])
    # Runtime de C++ estatico en Release. El driver se carga dentro de Chrome,
    # Teams o Zoom: con el runtime dinamico no cargaria en una PC sin el Visual
    # C++ Redistributable. softcam ademas enlaza msvcrt.lib a mano, lo que
    # arrastra vcruntime140.dll aunque se pida /MT: se quita.
    # Puede haber otros bloques (p. ej. <Midl>) antes de <ClCompile>; no se sale
    # del ItemDefinitionGroup de Release.
    release_clcompile = (r"(?s)(<ItemDefinitionGroup Condition=\"'\$\(Configuration\)\|\$\(Platform\)'"
                         r"=='Release\|(?:x64|Win32)'\">(?:(?!</ItemDefinitionGroup>).)*?<ClCompile>)")
    estatico = lambda m: m.group(1) + "\n      <RuntimeLibrary>MultiThreaded</RuntimeLibrary>"
    for proyecto in (os.path.join("softcam", "softcam.vcxproj"),
                     os.path.join("softcamcore", "softcamcore.vcxproj"),
                     os.path.join("baseclasses", "BaseClasses.vcxproj")):
        cambios = [(release_clcompile, estatico, 2)]
        if proyecto.startswith("softcam" + os.sep + "softcam.vcx"):
            cambios.append((r"msvcrt\.lib;", "", 2))
        reemplazar(os.path.join(destino, "src", proyecto), cambios)

    print(f"softcam personalizado en {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
