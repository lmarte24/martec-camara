"""Genera el icono de Martec Camara (icono.ico para Windows, icono.png para la
ventana) sin dependencias extra: un .ico puede contener imagenes PNG."""

import os
import struct

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
RECURSOS = os.path.join(AQUI, "..", "src", "martec_camara", "recursos")


def dibujar(t):
    esc = 4                                   # dibujar grande y reducir = bordes suaves
    s = t * esc
    im = np.zeros((s, s, 4), np.uint8)
    r = int(s * 0.22)
    azul = (170, 70, 15, 255)                 # BGRA
    cv2.rectangle(im, (r, 0), (s - r, s), azul, -1)
    cv2.rectangle(im, (0, r), (s, s - r), azul, -1)
    for cx, cy in ((r, r), (s - r, r), (r, s - r), (s - r, s - r)):
        cv2.circle(im, (cx, cy), r, azul, -1, cv2.LINE_AA)
    c = s // 2
    blanco = (255, 255, 255, 255)
    # Esquinas de encuadre.
    m, l, g = int(s * 0.17), int(s * 0.17), max(2, int(s * 0.055))
    for sx in (1, -1):
        for sy in (1, -1):
            x0 = m if sx > 0 else s - m
            y0 = m if sy > 0 else s - m
            cv2.line(im, (x0, y0), (x0 + sx * l, y0), blanco, g, cv2.LINE_AA)
            cv2.line(im, (x0, y0), (x0, y0 + sy * l), blanco, g, cv2.LINE_AA)
    # Lente.
    cv2.circle(im, (c, c), int(s * 0.2), blanco, max(2, int(s * 0.06)), cv2.LINE_AA)
    cv2.circle(im, (c, c), int(s * 0.07), blanco, -1, cv2.LINE_AA)
    return cv2.resize(im, (t, t), interpolation=cv2.INTER_AREA)


def main():
    tams = (16, 24, 32, 48, 64, 128, 256)
    pngs = [cv2.imencode(".png", dibujar(t))[1].tobytes() for t in tams]
    cab = struct.pack("<HHH", 0, 1, len(tams))
    dirs, datos = b"", b""
    off = 6 + 16 * len(tams)
    for t, png in zip(tams, pngs):
        dirs += struct.pack("<BBBBHHII", t % 256, t % 256, 0, 0, 1, 32, len(png), off)
        datos += png
        off += len(png)
    with open(os.path.join(AQUI, "icono.ico"), "wb") as fh:
        fh.write(cab + dirs + datos)
    os.makedirs(RECURSOS, exist_ok=True)
    # Tambien en los recursos de la app: lo usa el icono de la bandeja.
    with open(os.path.join(RECURSOS, "icono.ico"), "wb") as fh:
        fh.write(cab + dirs + datos)
    cv2.imwrite(os.path.join(RECURSOS, "icono.png"), dibujar(64))
    # Juego de imagenes para macOS: build/mac.sh lo convierte a .icns con iconutil.
    iconset = os.path.join(AQUI, "icono.iconset")
    os.makedirs(iconset, exist_ok=True)
    for t in (16, 32, 128, 256, 512):
        cv2.imwrite(os.path.join(iconset, f"icon_{t}x{t}.png"), dibujar(t))
        cv2.imwrite(os.path.join(iconset, f"icon_{t}x{t}@2x.png"), dibujar(t * 2))
    print("icono.ico, icono.png e icono.iconset generados")


if __name__ == "__main__":
    main()
