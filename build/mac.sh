#!/usr/bin/env bash
# Construye Martec Camara para macOS (Apple Silicon) y deja el .zip listo.
# Tiene que correr EN una Mac: PyInstaller no compila para Mac desde Windows.
#
#   bash build/mac.sh              (usa python3 del sistema; se recomienda 3.13)
#   PYTHON=python3.13 bash build/mac.sh
#
# Resultado: dist/MartecCamara-<version>-mac-arm64.zip

set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

paso() { printf '\n==> %s\n' "$1"; }

if [ ! -x .venv-build/bin/python ]; then
  paso "Creando entorno de construccion con $PYTHON"
  "$PYTHON" -m venv .venv-build
fi
PY=.venv-build/bin/python

paso "Instalando dependencias"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements-mac.txt

paso "Icono"
"$PY" build/hacer_icono.py
iconutil -c icns build/icono.iconset -o build/icono.icns

paso "Empaquetando con PyInstaller"
"$PY" -m PyInstaller --noconfirm --log-level WARN --distpath dist --workpath build/trabajo build/martec_camara.spec

paso "Firma ad hoc (obligatoria en Apple Silicon para que el binario arranque)"
codesign --force --deep --sign - dist/MartecCamara.app

VERSION=$("$PY" -c "import sys; sys.path.insert(0, 'src'); from martec_camara import sistema; print(sistema.VERSION)")
REPARTO="dist/MartecCamara-$VERSION-mac"
rm -rf "$REPARTO"
mkdir -p "$REPARTO"
cp -R dist/MartecCamara.app "$REPARTO/"

paso "Licencias, fuentes GPL y LEEME"
"$PY" build/completar_paquete.py "$REPARTO"

ZIP="dist/MartecCamara-$VERSION-mac-$(uname -m).zip"
paso "Comprimiendo $ZIP"
rm -f "$ZIP"
ditto -c -k --keepParent "$REPARTO" "$ZIP"
printf '\nListo: %s\n' "$ZIP"
