# Compila el driver moderno de camara virtual (Media Foundation, Windows 11).
#
#   powershell -ExecutionPolicy Bypass -File build\driver\compilar_driver_mf.ps1
#
# Es el que ven las apps de la Microsoft Store (WhatsApp, Camara de Windows),
# ademas de Chrome, Teams y Zoom. Basado en VCamSample (MIT), en driver_mf/.
# Requiere Visual Studio o Build Tools con C++; baja de nuget.org los paquetes
# de encabezados C++/WinRT y WIL.
# Resultado: build\driver\salida\MartecCamaraMF64.dll

$ErrorActionPreference = "Stop"
$aqui = $PSScriptRoot
$raiz = Resolve-Path (Join-Path $aqui "..\..")
$sol = Join-Path $raiz "driver_mf"
$proy = Join-Path $sol "MartecCamaraMF\VCamSampleSource.vcxproj"
$salida = Join-Path $aqui "salida"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "No hay Visual Studio / Build Tools con C++" }
$msbuild = Join-Path $vs "MSBuild\Current\Bin\MSBuild.exe"
$toolset = Get-ChildItem "$vs\MSBuild\Microsoft\VC\*\Platforms\x64\PlatformToolsets\*" -Directory |
           Where-Object { $_.Name -match '^v\d+$' } |
           Select-Object -ExpandProperty Name | Sort-Object -Descending | Select-Object -First 1

Write-Host "==> Restaurando paquetes NuGet (C++/WinRT, WIL)" -ForegroundColor Cyan
& $msbuild $proy /nologo /v:minimal -t:restore -p:RestorePackagesConfig=true "/p:SolutionDir=$sol\"
if ($LASTEXITCODE -ne 0) { throw "restaurar paquetes" }

Write-Host "==> Compilando x64 (toolset $toolset)" -ForegroundColor Cyan
& $msbuild $proy /nologo /m /v:minimal /p:Configuration=Release /p:Platform=x64 `
    /p:PlatformToolset=$toolset "/p:SolutionDir=$sol\" /p:TargetName=MartecCamaraMF
if ($LASTEXITCODE -ne 0) { throw "msbuild driver MF" }

$dll = Get-ChildItem $sol -Recurse -Filter MartecCamaraMF.dll | Where-Object { $_.FullName -match '\\x64\\Release\\' } |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $dll) { throw "no aparecio MartecCamaraMF.dll" }
New-Item -ItemType Directory -Force -Path $salida | Out-Null
Copy-Item $dll.FullName (Join-Path $salida "MartecCamaraMF64.dll") -Force
Copy-Item (Join-Path $sol "LICENSE-VCamSample.txt") (Join-Path $salida "VCamSample-LICENSE.txt") -Force
Get-Item (Join-Path $salida "MartecCamaraMF64.dll") | ForEach-Object { "{0,-24} {1,10:N0} bytes" -f $_.Name, $_.Length }
