"""Ponto de entrada do app instalado (EMPACOTAMENTO_INSTALADOR_WINDOWS.md §1 e §3.2).

    AiEditor.exe              abre o app: servidor + worker + janela própria (pywebview/WebView2) + bandeja
    AiEditor.exe --worker     só o worker (o próprio app o inicia como subprocesso)
    AiEditor.exe --headless   servidor + worker, sem janela (testes, teste de fumaça do build)

Em desenvolvimento: `python -m app.launcher [--headless]` (o fluxo de sempre, uvicorn + next dev, continua igual).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("aieditor.launcher")

MUTEX_NAME = "Local\\AiEditor.SingleInstance"
ERROR_ALREADY_EXISTS = 183
MB_OK, MB_ICONERROR, MB_YESNOCANCEL, MB_ICONQUESTION = 0x0, 0x10, 0x3, 0x20
MB_TOPMOST, MB_SETFOREGROUND = 0x40000, 0x10000
IDYES, IDNO = 6, 7
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0a0a0a;color:#e5e5e5;font-family:"Segoe UI",sans-serif}
.c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
.s{width:34px;height:34px;border:3px solid #333;border-top-color:#e63946;border-radius:50%;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}} h1{font-size:22px;font-weight:600;margin:0} p{margin:0;color:#888;font-size:13px}
</style></head><body><div class="c"><div class="s"></div><h1>Iniciando AiEditor…</h1>
<p>Preparando servidor, banco de dados e fila de produções</p></div></body></html>"""


# ---------------------------------------------------------------- infraestrutura

def _fix_std_streams() -> None:
    """Executável sem console (console=False): stdout/stderr são None e qualquer print/tqdm quebraria."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def setup_logging(name: str) -> Path:
    from .paths import FROZEN, logs_dir

    path = logs_dir() / f"{name}.log"
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    if not FROZEN:
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    return path


def message_box(text: str, title: str = "AiEditor", flags: int = MB_OK) -> int:
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST | MB_SETFOREGROUND)
    except Exception:  # noqa: BLE001
        return 0


def fatal(text: str, log_path: Path | None) -> None:
    log.error(text)
    where = f"\n\nDetalhes no log:\n{log_path}" if log_path else ""
    message_box(f"O AiEditor não conseguiu iniciar.\n\n{text}{where}", flags=MB_ICONERROR)
    os._exit(1)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))  # só na máquina local, nunca exposto na rede
        return s.getsockname()[1]


def wait_health(port: int, timeout: float = 30.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _lock_file() -> Path:
    from .paths import data_dir

    return data_dir() / "instance.json"


# ---------------------------------------------------------------- worker

def _watch_parent(parent_pid: int) -> None:
    """Se o app principal morrer (fechado, encerrado pelo instalador), o worker sai junto."""
    SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    if not handle:
        os._exit(0)
    kernel32.WaitForSingleObject(handle, INFINITE)
    os._exit(0)


def run_worker(parent_pid: int | None) -> None:
    setup_logging("worker")
    if parent_pid:
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True, name="parent-watch").start()
    from .worker.__main__ import main as worker_main

    worker_main()


class Worker:
    """Worker como subprocesso do próprio executável, reiniciado se cair (até 5 vezes)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.restarts = 0

    def command(self) -> list[str]:
        from .paths import FROZEN

        base = [sys.executable] if FROZEN else [sys.executable, "-m", "app.launcher"]
        return base + ["--worker", "--parent-pid", str(os.getpid())]

    def start(self) -> None:
        from .paths import REPO_ROOT

        self.proc = subprocess.Popen(self.command(), cwd=str(REPO_ROOT / "server") if (REPO_ROOT / "server").exists()
                                     else None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        log.info("worker iniciado (pid %s)", self.proc.pid)
        threading.Thread(target=self._supervise, daemon=True, name="worker-supervisor").start()

    def _supervise(self) -> None:
        proc = self.proc
        code = proc.wait() if proc else 0
        if self.stopping:
            return
        log.error("worker saiu inesperadamente (código %s)", code)
        if self.restarts < 5:
            self.restarts += 1
            time.sleep(2)
            self.start()

    def stop(self) -> None:
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # produção interrompida volta para a fila e retoma da etapa pendente
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------- servidor

class Server:
    def __init__(self, port: int) -> None:
        import uvicorn

        from .main import app

        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                                    access_log=False, timeout_graceful_shutdown=3))
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="uvicorn")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=6)


def start_backend(port: int) -> tuple[Server, Worker]:
    from .db import init_db

    init_db()  # banco + migrações antes de tudo (servidor e worker também rodam, de forma idempotente)
    server = Server(port)
    server.start()
    worker = Worker()
    worker.start()
    return server, worker


# ---------------------------------------------------------------- modos

def run_headless(port: int | None) -> None:
    log_path = setup_logging("server")
    port = port or free_port()
    server, worker = start_backend(port)
    if not wait_health(port):
        worker.stop()
        fatal("O servidor não respondeu em 30 s.", log_path)
    log.info("AiEditor (sem janela) em %s", server.url)
    print(f"AiEditor rodando em {server.url}", flush=True)
    try:
        while server.thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        server.stop()


def _already_running() -> bool:
    """Instância única: se o mutex já existe, só traz a janela existente para frente."""
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
        return False
    import httpx

    try:
        info = json.loads(_lock_file().read_text(encoding="utf-8"))
        httpx.post(f"http://127.0.0.1:{info['port']}/api/app/show", timeout=5)
    except Exception:  # noqa: BLE001 — a 1ª instância ainda está iniciando
        pass
    return True


_mutex = None


def run_gui() -> None:
    os.environ["AIEDITOR_DESKTOP"] = "1"
    log_path = setup_logging("launcher")
    from . import desktop, paths

    if _already_running():
        log.info("AiEditor já está aberto: janela trazida para frente")
        return
    log.info("AiEditor %s iniciando (dados em %s)", paths.app_version(), paths.data_dir())
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        fatal(f"Componente de janela indisponível: {e}", log_path)
        return
    port = free_port()
    try:
        server, worker = start_backend(port)
    except Exception as e:  # noqa: BLE001
        log.exception("falha ao iniciar o backend")
        fatal(f"Falha ao iniciar o servidor: {e}", log_path)
        return
    _lock_file().write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")

    state = {"quitting": False, "tray": None}
    window = webview.create_window("AiEditor", html=SPLASH, width=1440, height=900, min_size=(1100, 700),
                                   background_color="#0a0a0a")

    def show() -> None:
        window.show()
        window.restore()
        window.on_top = True  # traz para frente sem ficar "sempre no topo"
        window.on_top = False

    desktop.show_window = show

    def quit_app() -> None:
        state["quitting"] = True
        window.destroy()

    def busy_count() -> int:
        from sqlmodel import func, select

        from .db import session_scope
        from .models import Production

        with session_scope() as s:
            return s.exec(select(func.count()).select_from(Production).where(
                Production.status.in_(["queued", "running", "cancel_requested"]))).one()  # type: ignore[attr-defined]

    def on_closing():
        if state["quitting"]:
            return True
        n = busy_count()
        if not n:
            state["quitting"] = True
            return True
        choice = message_box(
            f"Há {n} produção(ões) em andamento.\n\n"
            "Sim: continuar o processamento em segundo plano (o AiEditor fica no ícone da bandeja).\n"
            "Não: encerrar agora (a produção retoma da etapa em que parou na próxima abertura).",
            flags=MB_YESNOCANCEL | MB_ICONQUESTION)
        if choice == IDYES:
            window.hide()
            if state["tray"]:
                try:
                    state["tray"].notify("Continuando em segundo plano. Clique no ícone para abrir.", "AiEditor")
                except Exception:  # noqa: BLE001
                    pass
            return False
        if choice == IDNO:
            state["quitting"] = True
            return True
        return False

    window.events.closing += on_closing
    state["tray"] = _start_tray(show, quit_app)

    def after_start() -> None:
        if wait_health(port):
            window.load_url(server.url)
        else:
            worker.stop()
            fatal("O servidor não respondeu em 30 s.", log_path)

    try:
        webview.start(after_start, private_mode=False, storage_path=str(paths.data_dir() / "webview"))
    finally:
        log.info("encerrando")
        if state["tray"]:
            try:
                state["tray"].stop()
            except Exception:  # noqa: BLE001
                pass
        worker.stop()
        server.stop()
        _lock_file().unlink(missing_ok=True)
        logging.shutdown()
        os._exit(0)


def _start_tray(show, quit_app):
    """Ícone na bandeja: Abrir AiEditor, Abrir pasta de dados, Abrir logs, Sair."""
    try:
        import pystray
        from PIL import Image

        from .paths import data_dir, logs_dir, resource_dir

        icon_file = resource_dir() / "resources" / "icon.png"
        image = Image.open(icon_file) if icon_file.exists() else Image.new("RGB", (64, 64), (230, 57, 70))
        menu = pystray.Menu(
            pystray.MenuItem("Abrir AiEditor", lambda: show(), default=True),
            pystray.MenuItem("Abrir pasta de dados", lambda: os.startfile(data_dir())),  # type: ignore[attr-defined]
            pystray.MenuItem("Abrir logs", lambda: os.startfile(logs_dir())),  # type: ignore[attr-defined]
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda icon: (icon.stop(), quit_app())),
        )
        tray = pystray.Icon("AiEditor", image, "AiEditor", menu)
        tray.run_detached()
        return tray
    except Exception:  # noqa: BLE001 — sem bandeja o app continua funcionando
        log.exception("bandeja indisponível")
        return None


def main() -> None:
    multiprocessing.freeze_support()
    _fix_std_streams()
    parser = argparse.ArgumentParser(prog="AiEditor")
    parser.add_argument("--worker", action="store_true", help="só o worker da fila de produções")
    parser.add_argument("--headless", action="store_true", help="servidor + worker sem janela")
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--port", type=int, default=None)
    args, _ = parser.parse_known_args()
    if args.worker:
        run_worker(args.parent_pid)
    elif args.headless:
        run_headless(args.port)
    else:
        run_gui()


if __name__ == "__main__":
    main()
