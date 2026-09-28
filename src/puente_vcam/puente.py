# Puente de Martec Camara hacia la camara virtual de OBS.
#
# Copyright (C) 2026 Martec
#
# Este programa es software libre: puede redistribuirlo y/o modificarlo bajo
# los terminos de la Licencia Publica General GNU version 2, publicada por la
# Free Software Foundation. Se distribuye bajo esta licencia porque usa
# pyvirtualcam, que es GPL-2.0. Ver LICENSE en esta carpeta.
#
# Es un programa aparte de la app principal a proposito: la app le pasa los
# cuadros por una tuberia y este los publica en "OBS Virtual Camera".
#
# Protocolo:
#   argumentos:  ancho alto fps
#   entrada:     cuadros I420 seguidos, cada uno de ancho*alto*3/2 bytes
#   salida:      "LISTO <dispositivo>" al abrir la camara virtual, o
#                "ERROR <mensaje>" y termina con codigo 3.

import sys


def main():
    if len(sys.argv) != 4:
        print("Este programa lo usa Martec Cámara; no hace falta abrirlo.")
        return 2
    try:
        w, h, fps = (int(v) for v in sys.argv[1:4])
    except ValueError:
        print("ERROR argumentos invalidos", flush=True)
        return 2
    try:
        import numpy as np
        import pyvirtualcam
        cam = pyvirtualcam.Camera(width=w, height=h, fps=fps,
                                  fmt=pyvirtualcam.PixelFormat.I420,
                                  print_fps=False)
    except Exception as e:
        print(f"ERROR {e}", flush=True)
        return 3
    print(f"LISTO {cam.device}", flush=True)

    tam = w * h * 3 // 2
    entrada = sys.stdin.buffer
    buf = bytearray(tam)
    vista = memoryview(buf)
    cuadro = np.frombuffer(buf, dtype=np.uint8)
    with cam:
        while True:
            leido = 0
            while leido < tam:
                k = entrada.readinto(vista[leido:])
                if not k:
                    return 0            # la app cerro la tuberia
                leido += k
            cam.send(cuadro)


if __name__ == "__main__":
    sys.exit(main())
