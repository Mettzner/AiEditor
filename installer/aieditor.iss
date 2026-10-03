; Instalador do AiEditor (Inno Setup 6, gratuito) - EMPACOTAMENTO_INSTALADOR_WINDOWS.md §6
; Gerado pelo build.ps1:  ISCC.exe /DMyAppVersion=1.0.0 installer\aieditor.iss
; Instala por usuário em %LOCALAPPDATA%\Programs\AiEditor, sem pedir administrador.
; Os dados ficam em %LOCALAPPDATA%\AiEditor e sobrevivem a atualizações.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "AiEditor"
#define MyAppExe "AiEditor.exe"

[Setup]
; AppId fixo: identifica o app entre versões (não mudar, ou a atualização vira uma 2ª instalação)
AppId={{51E5F15D-7C8D-42F7-A3E0-602A4E9ED139}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=AiEditor
AppPublisherURL=https://mettzner.github.io/AiEditor/
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsedUserAreasWarning=no
OutputDir=..\dist
OutputBaseFilename=AiEditor-Setup-{#MyAppVersion}
SetupIconFile=..\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
; compressor num processo 64-bit separado: o ISCC é 32-bit e fica sem memória com ~1 GB de entrada
LZMAUseSeparateProcess=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; o próprio [Code] encerra o app (janela, servidor e worker) antes de substituir os arquivos
CloseApplications=no
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; atualização limpa: remove as bibliotecas da versão anterior (os dados do usuário ficam em outra pasta)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\AiEditor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\cache\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando o Microsoft Edge WebView2 Runtime..."; Check: NeedsWebView2; Flags: waituntilterminated
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Version(Root: Integer; Key: String): String;
begin
  if not RegQueryStringValue(Root, Key, 'pv', Result) then
    Result := '';
end;

{ WebView2 já vem no Windows 10/11 atualizados; o bootstrapper oficial só roda se faltar (Windows 10 antigo). }
function NeedsWebView2: Boolean;
var
  V: String;
begin
  V := WebView2Version(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
  if (V = '') or (V = '0.0.0.0') then
    V := WebView2Version(HKLM, WebView2Key);
  if (V = '') or (V = '0.0.0.0') then
    V := WebView2Version(HKCU, WebView2Key);
  Result := (V = '') or (V = '0.0.0.0');
end;

procedure CloseRunningApp;
var
  ResultCode: Integer;
begin
  { encerra janela, servidor e worker (todos são AiEditor.exe); produções interrompidas retomam depois }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(800);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningApp;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseRunningApp;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#MyAppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Apagar também os seus dados do AiEditor?' + #13#10#13#10 +
                'Canais, produções, vídeos, cache e configurações em:' + #13#10 + DataDir + #13#10#13#10 +
                'As chaves de API ficam no Gerenciador de Credenciais do Windows e não são apagadas.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
