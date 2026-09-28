# Construye el instalador de Martec Camara para Windows.
#
#   powershell -ExecutionPolicy Bypass -File build\windows.ps1
#   powershell -ExecutionPolicy Bypass -File build\windows.ps1 -Python C:\Python314\python.exe
#
# Requiere: Python 3.13+, Visual Studio o Build Tools con C++ (para el driver)
# e Inno Setup 6 (winget install JRSoftware.InnoSetup).
#
# Resultado: dist\MartecCamara-<version>-instalador.exe  (un solo archivo)

param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Paso($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Revisar($que) { if ($LASTEXITCODE -ne 0) { throw "Fallo: $que (codigo $LASTEXITCODE)" } }

$venv = Join-Path $raiz ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Paso "Creando entorno de construccion con $Python"
    & $Python -m venv $venv; Revisar "crear venv"
}

Paso "Instalando dependencias"
& $py -m pip install --quiet --upgrade pip; Revisar "actualizar pip"
& $py -m pip install --quiet -r requirements-windows.txt; Revisar "instalar dependencias"

Paso "Icono"
& $py build\hacer_icono.py; Revisar "icono"

Paso "Driver clasico de camara virtual (DirectShow, softcam)"
& powershell -NoProfile -ExecutionPolicy Bypass -File build\driver\compilar_driver.ps1 -Python $py
Revisar "compilar driver"

Paso "Driver moderno de camara virtual (Media Foundation, Windows 11)"
& powershell -NoProfile -ExecutionPolicy Bypass -File build\driver\compilar_driver_mf.ps1
Revisar "compilar driver MF"

Paso "Empaquetando la app con PyInstaller"
& $py -m PyInstaller --noconfirm --log-level WARN --distpath dist --workpath build\trabajo build\martec_camara.spec
Revisar "pyinstaller"

$carpeta = Join-Path $raiz "dist\MartecCamara"
Paso "Licencias y LEEME"
& $py build\completar_paquete.py $carpeta; Revisar "completar paquete"

Paso "Instalador (Inno Setup)"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
if (-not $iscc) { throw "No se encontro Inno Setup 6 (winget install JRSoftware.InnoSetup)" }
# Cada compilacion instala sus drivers en su propia carpeta (ver instalador.iss).
$build = Get-Date -Format "yyyyMMdd-HHmm"
& $iscc /Q "/DBuild=$build" build\instalador.iss; Revisar "inno setup"

$version = & $py -c "import sys; sys.path.insert(0, 'src'); from martec_camara import sistema; print(sistema.VERSION)"
$setup = Join-Path $raiz "dist\MartecCamara-$version-instalador.exe"
$mb = (Get-Item $setup).Length / 1MB
Write-Host ("`nListo: {0} ({1:N1} MB)" -f $setup, $mb) -ForegroundColor Green
