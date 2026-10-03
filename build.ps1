<#
.SYNOPSIS
  Gera o instalador do AiEditor (dist\AiEditor-Setup-<versão>.exe) com um único comando.
  EMPACOTAMENTO_INSTALADOR_WINDOWS.md §7.

.DESCRIPTION
  1. Lê a versão de VERSION e a injeta no frontend, no backend (pyproject) e no instalador.
  2. npm ci + npm run build (export estático em web\out).
  3. Monta build\stage: web, recursos, VERSION e informações de versão do .exe.
  4. Baixa (com cache em build\cache) o FFmpeg (gyan.dev, essentials: libx264, AMF, libass) e o bootstrapper do
     WebView2, e copia ffmpeg.exe/ffprobe.exe + licença para build\stage\bin.
  5. PyInstaller (aieditor.spec) num venv de build próprio, a partir de server\requirements.lock.
  6. Teste de fumaça: dist\AiEditor\AiEditor.exe --headless, espera o /api/health e chama endpoints simples.
  7. ISCC installer\aieditor.iss → dist\AiEditor-Setup-<versão>.exe.

.PARAMETER EmbedGoogleClientFromKeyring
  Embute o client OAuth do Google ("App para computador") lido do Gerenciador de Credenciais (a mesma chave da
  Configuração). Alternativa: deixar o JSON em installer\google_oauth_client.json (ignorado pelo git).
  No fluxo para apps instalados o Google não trata esse client_secret como segredo; cada amigo entra com a
  própria conta. NUNCA embutir chaves de API: cada usuário informa as suas no assistente inicial.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\build.ps1 -EmbedGoogleClientFromKeyring
#>
param(
    [switch]$EmbedGoogleClientFromKeyring,
    [switch]$SkipWeb,
    [switch]$SkipSmokeTest,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
$build = Join-Path $root "build"
$stage = Join-Path $build "stage"
$cache = Join-Path $build "cache"
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $build, $cache, $dist | Out-Null

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERRO: $msg" -ForegroundColor Red; exit 1 }
function Run($exe, [string[]]$argList) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { Fail "$exe $($argList -join ' ') saiu com código $LASTEXITCODE" }
}

# ---------------------------------------------------------------- 1. versão (fonte única: VERSION)
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { Fail "VERSION inválida: '$version' (use 1.2.3)" }
Step "AiEditor $version"
$pyproject = Join-Path $root "server\pyproject.toml"
$content = Get-Content $pyproject -Raw
$updated = [regex]::Replace($content, '(?m)^version = "[^"]*"', "version = `"$version`"")
if ($updated -ne $content) { [IO.File]::WriteAllText($pyproject, $updated, (New-Object Text.UTF8Encoding $false)) }

# ---------------------------------------------------------------- 2. frontend estático
if (-not $SkipWeb) {
    Step "Frontend: npm ci + export estático"
    Push-Location (Join-Path $root "web")
    try {
        # npm ci apaga o node_modules inteiro e falha (deixando-o pela metade) se o `next dev` estiver aberto;
        # com node_modules presente, npm install só completa o que falta, respeitando o package-lock.json
        if (Test-Path "node_modules\next\package.json") {
            Run "npm.cmd" @("install", "--no-audit", "--no-fund")
        } else {
            Run "npm.cmd" @("ci", "--no-audit", "--no-fund")
        }
        $env:NEXT_PUBLIC_APP_VERSION = $version
        # API relativa (mesma origem no app instalado); a variável do processo vence o web\.env.local do dev
        $env:NEXT_PUBLIC_API_URL = "/api"
        Run "npm.cmd" @("run", "build")
    } finally { Pop-Location }
}
$webOut = Join-Path $root "web\out"
if (-not (Test-Path (Join-Path $webOut "index.html"))) { Fail "web\out\index.html não existe (rode sem -SkipWeb)" }
$leak = Get-ChildItem $webOut -Recurse -Filter *.js | Select-String -SimpleMatch "localhost:8000" -List | Select-Object -First 1
if ($leak) { Fail "o export do frontend aponta para localhost:8000 ($($leak.Path)); a API precisa ser relativa (/api)" }

# ---------------------------------------------------------------- 3. stage
Step "Montando build\stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force (Join-Path $stage "bin"), (Join-Path $stage "resources") | Out-Null
Copy-Item -Recurse $webOut (Join-Path $stage "web")
Copy-Item (Join-Path $root "resources\*") (Join-Path $stage "resources") -Recurse
Set-Content -Path (Join-Path $stage "VERSION") -Value $version -NoNewline -Encoding ascii

$v = $version.Split(".")
$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($v[0]), $($v[1]), $($v[2]), 0), prodvers=($($v[0]), $($v[1]), $($v[2]), 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'AiEditor'),
      StringStruct('FileDescription', 'AiEditor - roteiro e narração viram vídeo'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'AiEditor'),
      StringStruct('OriginalFilename', 'AiEditor.exe'),
      StringStruct('ProductName', 'AiEditor'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
"@
Set-Content -Path (Join-Path $stage "version_info.txt") -Value $versionInfo -Encoding utf8

# client OAuth do Google (opcional)
$googleJson = Join-Path $root "installer\google_oauth_client.json"
$stagedGoogle = Join-Path $stage "resources\google_oauth_client.json"
if (Test-Path $googleJson) {
    Copy-Item $googleJson $stagedGoogle
    Write-Host "Client OAuth do Google embutido (installer\google_oauth_client.json)"
} elseif ($EmbedGoogleClientFromKeyring) {
    $devPy = Join-Path $root "server\.venv\Scripts\python.exe"
    $script = "import json,keyring,sys; raw=keyring.get_password('AiEditor','google_oauth_client') or ''; " +
              "d=json.loads(raw) if raw else {}; " +
              "sys.exit('o client do Google na Configuração não é do tipo App para computador') if raw and 'installed' not in d else None; " +
              "open(sys.argv[1],'w',encoding='utf-8').write(raw) if raw else sys.exit('nenhum client OAuth do Google no keyring')"
    Run $devPy @("-c", $script, $stagedGoogle)
    Write-Host "Client OAuth do Google (App para computador) embutido a partir do keyring"
} else {
    Write-Host "Sem client OAuth do Google embutido: cada usuário terá de colar o JSON na Configuração" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 4. FFmpeg + WebView2 (cache)
Step "FFmpeg e WebView2"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ffZip = Join-Path $cache "ffmpeg-release-essentials.zip"
if (-not (Test-Path $ffZip)) {
    Write-Host "Baixando FFmpeg (gyan.dev, release essentials)..."
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "$ffZip.part"
    Move-Item "$ffZip.part" $ffZip
}
$ffDir = Join-Path $cache "ffmpeg"
if (-not (Test-Path (Join-Path $ffDir "ffmpeg.exe"))) {
    $tmp = Join-Path $cache "ffmpeg-extract"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $ffZip $tmp
    $binDir = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1 | ForEach-Object { $_.DirectoryName }
    New-Item -ItemType Directory -Force $ffDir | Out-Null
    Copy-Item (Join-Path $binDir "ffmpeg.exe"), (Join-Path $binDir "ffprobe.exe") $ffDir
    $license = Get-ChildItem $tmp -Recurse -Include "LICENSE*", "COPYING*" | Select-Object -First 1
    if ($license) { Copy-Item $license.FullName (Join-Path $ffDir "FFMPEG-LICENSE.txt") }
    Remove-Item -Recurse -Force $tmp
}
Copy-Item (Join-Path $ffDir "*") (Join-Path $stage "bin")
Set-Content -Path (Join-Path $stage "bin\LEIA-ME.txt") -Encoding utf8 -Value @"
FFmpeg (build release essentials de https://www.gyan.dev/ffmpeg/builds/), distribuído sob a GPL v3 (inclui libx264).
Licença completa em FFMPEG-LICENSE.txt. Código-fonte: https://ffmpeg.org/download.html
Antes de vender o AiEditor, revisar a conformidade da licença ou trocar por uma build LGPL com outro encoder.
"@
$wv2 = Join-Path $cache "MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $wv2)) {
    Write-Host "Baixando o bootstrapper do WebView2 Runtime..."
    Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $wv2
}

# ---------------------------------------------------------------- 5. PyInstaller (venv de build fixado)
Step "Python de build (server\requirements.lock)"
$venv = Join-Path $build "venv"
$py = Join-Path $venv "Scripts\python.exe"
$lock = Join-Path $root "server\requirements.lock"
$lockStamp = Join-Path $venv "lock.sha256"
$lockHash = (Get-FileHash $lock -Algorithm SHA256).Hash
if (-not (Test-Path $py)) {
    $base = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $base = (& py -3.13 -c "import sys; print(sys.executable)") 2>$null }
    if (-not $base) { $base = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe" }
    if (-not (Test-Path $base)) { Fail "Python 3.13 não encontrado (instale de python.org)" }
    Run $base @("-m", "venv", $venv)
}
if (-not (Test-Path $lockStamp) -or (Get-Content $lockStamp -Raw).Trim() -ne $lockHash) {
    Run $py @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", $lock)
    Set-Content $lockStamp $lockHash
}

Step "PyInstaller (onedir, sem UPX, sem console)"
$appDist = Join-Path $dist "AiEditor"
if (Test-Path $appDist) { Remove-Item -Recurse -Force $appDist }
Run $py @("-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
          "--distpath", $dist, "--workpath", (Join-Path $build "pyinstaller"), (Join-Path $root "aieditor.spec"))
$exe = Join-Path $appDist "AiEditor.exe"
if (-not (Test-Path $exe)) { Fail "AiEditor.exe não foi gerado" }

# ---------------------------------------------------------------- 6. teste de fumaça
if (-not $SkipSmokeTest) {
    Step "Teste de fumaça (--headless, dados temporários)"
    $smokeData = Join-Path $build "smoke-data"
    if (Test-Path $smokeData) { Remove-Item -Recurse -Force $smokeData }
    $port = 18765
    $env:AIEDITOR_DATA = $smokeData
    $proc = Start-Process $exe -ArgumentList "--headless", "--port", $port -PassThru
    Remove-Item Env:AIEDITOR_DATA
    $ok = $false
    try {
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline -and -not $ok) {
            Start-Sleep -Milliseconds 700
            try {
                $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 3
                $ok = [bool]$health.ok
            } catch { if ($proc.HasExited) { break } }
        }
        if (-not $ok) { Fail "o app empacotado não respondeu ao /api/health (veja $smokeData\logs)" }
        $dirs = Invoke-RestMethod "http://127.0.0.1:$port/api/directions" -TimeoutSec 10
        $page = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 10
        $info = Invoke-RestMethod "http://127.0.0.1:$port/api/app/info" -TimeoutSec 30
        if ($health.version -ne $version) { Fail "versão reportada $($health.version) <> $version" }
        if (-not $health.ffmpeg) { Fail "o FFmpeg empacotado não foi encontrado" }
        if ($page.Content -notmatch "AiEditor") { Fail "o frontend estático não foi servido" }
        Write-Host ("OK: versão {0}, {1} direção(ões), frontend servido, ffmpeg empacotado, AMF neste PC: {2}" -f
            $health.version, @($dirs).Count, $info.ffmpeg.amf) -ForegroundColor Green
    } finally {
        & taskkill.exe /PID $proc.Id /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 1
    }
}

# ---------------------------------------------------------------- 7. instalador
if (-not $SkipInstaller) {
    Step "Inno Setup"
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { Fail "Inno Setup 6 não encontrado (winget install JRSoftware.InnoSetup)" }
    Run $iscc @("/Q", "/DMyAppVersion=$version", (Join-Path $root "installer\aieditor.iss"))
    $setup = Join-Path $dist "AiEditor-Setup-$version.exe"
    $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Step "Pronto: $setup ($mb MB)"
}
