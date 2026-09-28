; Instalador de Martec Cámara para Windows (Inno Setup 6).
;
; Un solo archivo que deja todo listo: la app y su driver de cámara virtual
; "Martec Cámara". No depende de nada más: ni OBS, ni Python, ni el Visual C++
; Redistributable (el driver se compila con el runtime incluido).
;
; Se construye con build\windows.ps1. Resultado: dist\MartecCamara-<version>-instalador.exe

#define Nombre "Martec Cámara"
#define Version "0.1.0"
#define Exe "MartecCamara.exe"
; Identificador de compilación: los drivers de cada compilación van a su propia
; carpeta (driver\<Build>) para no reemplazar una DLL que Chrome, Teams o el
; servicio de cámaras tengan cargada. windows.ps1 lo pasa con /DBuild=...
#ifndef Build
  #define Build "dev"
#endif

[Setup]
; AppId FIJO: las versiones nuevas se instalan como actualización encima.
AppId={{F58E6B5F-AF87-4171-838C-79347E2B73F5}
AppName={#Nombre}
AppVersion={#Version}
AppVerName={#Nombre} {#Version}
AppPublisher=Martec
VersionInfoVersion={#Version}
VersionInfoCompany=Martec
VersionInfoDescription=Instalador de {#Nombre}
DefaultDirName={autopf}\Martec Camara
DefaultGroupName={#Nombre}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=MartecCamara-{#Version}-instalador
SetupIconFile=icono.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nombre}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Registrar el driver de cámara virtual en Windows requiere administrador.
PrivilegesRequired=admin
; Si la app está abierta, pedir cerrarla antes de instalar o actualizar.
AppMutex=MartecCamaraMutex
; NO cerrar otras apps: Chrome, Slack, Teams, Claude... cargan el driver de
; cámara con solo listar las cámaras, y el instalador quería cerrarlas (en modo
; silencioso abortaba). Si un driver está en uso, restartreplace lo reemplaza
; al reiniciar, como cualquier driver de cámara.
CloseApplications=no
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "arranque"; Description: "Abrir {#Nombre} al iniciar Windows (recomendado)"; GroupDescription: "Inicio:"
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "..\dist\MartecCamara\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Driver clásico de cámara virtual (DirectShow), solo Windows 10. regserver lo
; registra al instalar y lo quita al desinstalar.
Source: "driver\salida\MartecCamaraVCam64.dll"; DestDir: "{app}\driver\{#Build}"; Flags: ignoreversion regserver uninsrestartdelete 64bit; Check: not EsWindows11
; La versión de 32 bits es para apps de 32 bits, que todavía existen.
Source: "driver\salida\MartecCamaraVCam32.dll"; DestDir: "{app}\driver\{#Build}"; Flags: ignoreversion regserver uninsrestartdelete 32bit; Check: not EsWindows11
; Driver moderno (Media Foundation), solo Windows 11. Lo ven todas las apps: las
; de la Microsoft Store como WhatsApp y también las de DirectShow como Chrome o
; Zoom, por el puente del servicio de cámaras. Con el clásico además salían dos
; "Martec Cámara" en cada lista. La cámara en sí la crea la app al abrirse; aquí
; solo se registra la DLL (requiere administrador).
Source: "driver\salida\MartecCamaraMF64.dll"; DestDir: "{app}\driver\{#Build}"; Flags: ignoreversion regserver uninsrestartdelete 64bit; Check: EsWindows11

[Icons]
Name: "{autoprograms}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"; Tasks: escritorio

[Registry]
; Mismo valor que usa la casilla "Iniciar con el sistema" de la app.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MartecCamara"; ValueData: """{app}\{#Exe}"" --oculto"; Flags: uninsdeletevalue; Tasks: arranque

[Run]
; runasoriginaluser: la app corre como el usuario normal, no como administrador
; (sus ajustes y el permiso de cámara son del usuario).
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre} ahora"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
function EsWindows11: Boolean;
var
  V: TWindowsVersion;
begin
  GetWindowsVersionEx(V);
  Result := (V.Major = 10) and (V.Build >= 22000);
end;

{ Tras instalar: borrar los drivers de compilaciones anteriores. Si alguno
  sigue cargado en Chrome, Teams o el servicio de cámaras, no se puede borrar
  ahora y queda para la próxima actualización (no molesta: ya no está
  registrado). También limpia las DLL sueltas de la versión 0.1.0 inicial. }
procedure LimpiarDriversViejos();
var
  Busq: TFindRec;
  Base: String;
begin
  Base := ExpandConstant('{app}\driver');
  if FindFirst(Base + '\*', Busq) then
  begin
    try
      repeat
        if (Busq.Name <> '.') and (Busq.Name <> '..') and (Busq.Name <> '{#Build}') then
        begin
          if (Busq.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            DelTree(Base + '\' + Busq.Name, True, True, True)
          else
            DeleteFile(Base + '\' + Busq.Name);
        end;
      until not FindNext(Busq);
    finally
      FindClose(Busq);
    end;
  end;
end;

{ Una instalación anterior pudo dejar programado re-registrar los drivers en la
  carpeta vieja al próximo inicio de sesión (Inno Setup lo hace cuando algún
  archivo estaba en uso). Con las carpetas por compilación eso ya no pasa, pero
  hay que cancelar ese pendiente para que no pise el registro nuevo. Solo se
  cancelan los pendientes cuya lista de archivos menciona Martec Camara. }
procedure CancelarRegistroDiferidoViejo();
var
  Nombres: TArrayOfString;
  I, Fin: Integer;
  Clave, Valor, Exe, Lst: String;
  Contenido: AnsiString;
begin
  Clave := 'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce';
  if not RegGetValueNames(HKLM32, Clave, Nombres) then
    Exit;
  for I := 0 to GetArrayLength(Nombres) - 1 do
  begin
    if (Pos('InnoSetupRegFile', Nombres[I]) = 1) and
       RegQueryStringValue(HKLM32, Clave, Nombres[I], Valor) then
    begin
      { Valor: "C:\WINDOWS\is-XXXX.exe" /REG /REGSVRMODE }
      Fin := Pos('" ', Valor);
      if (Copy(Valor, 1, 1) = '"') and (Fin > 2) then
      begin
        Exe := Copy(Valor, 2, Fin - 2);
        Lst := ChangeFileExt(Exe, '.lst');
        if LoadStringFromFile(Lst, Contenido) and (Pos('Martec Camara', String(Contenido)) > 0) then
        begin
          RegDeleteValue(HKLM32, Clave, Nombres[I]);
          DeleteFile(Lst);
          DeleteFile(ChangeFileExt(Exe, '.msg'));
          DeleteFile(Exe);
          Log('Cancelado registro diferido viejo: ' + Nombres[I]);
        end;
      end;
    end;
  end;
end;

{ Windows 11: quitar la cámara clásica que registraron versiones anteriores
  (ahí va solo la moderna). Primero con su propio des-registro; después se
  borran las claves por si su DLL ya no estaba. Son claves solo de Martec. }
const
  CLSID_CLASICA = '{24BE734C-01F4-4D96-83D8-F93DC74CE46C}';
  CATEGORIA_CAMARAS = '{860BB310-5D01-11d0-BD3B-00A0C911CE86}';

procedure QuitarClasicaDe(Raiz: Integer; Es64: Boolean);
var
  Ruta: String;
begin
  if RegQueryStringValue(Raiz, 'SOFTWARE\Classes\CLSID\' + CLSID_CLASICA + '\InprocServer32', '', Ruta) then
  begin
    if FileExists(Ruta) then
      UnregisterServer(Es64, Ruta, False);
    Log('Quitada la cámara clásica: ' + Ruta);
  end;
  RegDeleteKeyIncludingSubkeys(Raiz, 'SOFTWARE\Classes\CLSID\' + CATEGORIA_CAMARAS + '\Instance\' + CLSID_CLASICA);
  RegDeleteKeyIncludingSubkeys(Raiz, 'SOFTWARE\Classes\CLSID\' + CLSID_CLASICA);
end;

procedure QuitarCamaraClasica();
begin
  QuitarClasicaDe(HKLM64, True);
  QuitarClasicaDe(HKLM32, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    CancelarRegistroDiferidoViejo();
    { Antes de limpiar: la DLL registrada está en la carpeta de la versión anterior. }
    if EsWindows11 then
      QuitarCamaraClasica();
    LimpiarDriversViejos();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { La casilla de la app puede haber creado el arranque automático aunque la
    tarea del instalador no se haya elegido: se borra igual al desinstalar. }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MartecCamara');
end;
