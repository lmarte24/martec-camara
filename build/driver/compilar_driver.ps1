# Compila el driver de camara virtual "Martec Camara" (softcam personalizado).
#
#   powershell -ExecutionPolicy Bypass -File build\driver\compilar_driver.ps1
#
# Requiere Visual Studio o Build Tools con "Desarrollo de escritorio con C++".
# Resultado: build\driver\salida\MartecCamaraVCam64.dll y MartecCamaraVCam32.dll

param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$aqui = $PSScriptRoot
$raiz = Resolve-Path (Join-Path $aqui "..\..")
$fuente = Join-Path $raiz "build\trabajo\softcam-martec"
$salida = Join-Path $aqui "salida"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "No hay Visual Studio / Build Tools con C++" }
$msbuild = Join-Path $vs "MSBuild\Current\Bin\MSBuild.exe"
# El toolset mas nuevo instalado (v145 en Build Tools 2026, v143 en 2022).
$toolset = Get-ChildItem "$vs\MSBuild\Microsoft\VC\*\Platforms\x64\PlatformToolsets\*" -Directory |
           Where-Object { $_.Name -match '^v\d+$' } |
           Select-Object -ExpandProperty Name | Sort-Object -Descending | Select-Object -First 1
if (-not $toolset) { throw "no se encontro ningun toolset de C++" }
Write-Host "MSBuild: $msbuild  toolset: $toolset"

& $Python (Join-Path $aqui "personalizar_softcam.py") $fuente
if ($LASTEXITCODE -ne 0) { throw "personalizar softcam" }

New-Item -ItemType Directory -Force -Path $salida | Out-Null
$props = Join-Path $aqui "estatico.props"
foreach ($plat in @(@{P = "x64"; Bits = "64"}, @{P = "Win32"; Bits = "32"})) {
    Write-Host "`n==> Compilando $($plat.P)" -ForegroundColor Cyan
    & $msbuild (Join-Path $fuente "src\softcam\softcam.vcxproj") /nologo /m /v:minimal `
        /p:Configuration=Release /p:Platform=$($plat.P) /p:PlatformToolset=$toolset `
        "/p:SolutionDir=$fuente\" "/p:ForceImportBeforeCppTargets=$props"
    if ($LASTEXITCODE -ne 0) { throw "msbuild $($plat.P)" }
    $dll = Get-ChildItem $fuente -Recurse -Filter softcam.dll |
           Where-Object { $_.FullName -match "\\$($plat.P)\\Release\\" } | Select-Object -First 1
    if (-not $dll) { throw "no aparecio softcam.dll de $($plat.P)" }
    Copy-Item $dll.FullName (Join-Path $salida "MartecCamaraVCam$($plat.Bits).dll") -Force
}
Copy-Item (Join-Path $raiz "third_party\softcam\LICENSE") (Join-Path $salida "softcam-LICENSE.txt") -Force
Get-ChildItem $salida | ForEach-Object { "{0,-28} {1,10:N0} bytes" -f $_.Name, $_.Length }
