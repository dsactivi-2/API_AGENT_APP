"""Startet den lokalen Server und öffnet das App-Fenster (pywebview), sonst den Browser."""

from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup_logging() -> Path:
    if sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs"
    else:
        log_dir = Path.home() / ".local" / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "AgentChat.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    return log_file


def main() -> None:
    log_file = setup_logging()
    os.environ.setdefault("AGENTCHAT_TOKEN", secrets.token_urlsafe(32))
    token = os.environ["AGENTCHAT_TOKEN"]
    port = int(os.environ.get("AGENTCHAT_PORT") or free_port())

    import uvicorn

    from app.server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/#token={token}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    logging.getLogger("agentchat").info("Server läuft auf Port %s, Log: %s", port, log_file)

    if os.environ.get("AGENTCHAT_HEADLESS"):
        print(url, flush=True)
        thread.join()
        return
    try:
        import webview  # pywebview

        class Api:
            """Wird im Fenster als window.pywebview.api bereitgestellt."""

            def pick_folder(self):
                result = window.create_file_dialog(webview.FOLDER_DIALOG)
                return result[0] if result else None

        window = webview.create_window("Agent Chat", url, width=1280, height=860, min_size=(820, 560), js_api=Api())
        webview.start()
    except Exception:
        logging.getLogger("agentchat").exception("pywebview nicht verfügbar, öffne Browser")
        webbrowser.open(url)
        thread.join()
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
