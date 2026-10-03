# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller do AiEditor (EMPACOTAMENTO_INSTALADOR_WINDOWS.md §5).

Gerado pelo build.ps1, que antes prepara build/stage/ (web exportado, ffmpeg, recursos, VERSION, versão do exe).
Modo --onedir (abre mais rápido e gera menos falso positivo de antivírus), sem UPX, sem console.
Saída: dist/AiEditor/AiEditor.exe + dist/AiEditor/_internal/ (Python, dependências e recursos).
"""
import os
from glob import glob

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(SPECPATH)
SERVER = os.path.join(ROOT, "server")
STAGE = os.path.join(ROOT, "build", "stage")

datas = [
    (os.path.join(STAGE, "web"), "web"),
    (os.path.join(STAGE, "bin"), "bin"),
    (os.path.join(STAGE, "resources"), "resources"),
    (os.path.join(STAGE, "VERSION"), "."),
]
# templates de direção (manifest, prompt, imagem do card): lidos ao lado do pacote app.directions
for f in glob(os.path.join(SERVER, "app", "directions", "*", "*")):
    if os.path.isfile(f) and not f.endswith((".py", ".pyc")):
        datas.append((f, os.path.relpath(os.path.dirname(f), SERVER)))

binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn") + [
    "keyring.backends.Windows", "win32ctypes.core", "win32ctypes.pywin32", "google_auth_oauthlib",
    "googleapiclient.discovery", "google.genai", "anthropic", "PIL.Image", "sqlalchemy.dialects.sqlite",
    "multipart", "python_multipart", "webview.platforms.edgechromium", "webview.platforms.winforms",
    "pystray._win32", "clr",
]
for pkg in ("faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av", "lingua", "webview", "pystray",
            "yt_dlp", "huggingface_hub", "tzdata"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SERVER, "aieditor_main.py")],
    pathex=[SERVER],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "jupyter", "notebook"],
    noarchive=False,
)

# googleapiclient traz ~600 documentos de descoberta (~109 MB); o app só usa o Drive v3
KEEP_DISCOVERY = {"drive.v3.json"}
a.datas = [d for d in a.datas
           if "discovery_cache" not in d[0].replace("\\", "/") or os.path.basename(d[0]) in KEEP_DISCOVERY
           or not d[0].endswith(".json")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AiEditor",
    icon=os.path.join(STAGE, "resources", "icon.ico"),
    version=os.path.join(STAGE, "version_info.txt"),
    console=False,
    upx=False,
    debug=False,
    strip=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AiEditor")
