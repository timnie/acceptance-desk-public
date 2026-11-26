"""Run the native window and the shared loopback workbench."""
import os
import sys
import threading
from functools import partial
from pathlib import Path

from desktop.resources import verify_model

NAME = "AI 改稿验收台"
BUNDLE_ID = "org.acceptancedesk.workbench"


def save_before_close(window, close, warn):
    pending = threading.Lock()
    approved = threading.Event()

    def flush_and_close():
        try:
            if window.events.loaded.is_set():
                close.finished.clear()
                began = window.evaluate_js(
                    "(() => {const b = document.querySelector('.desktop-close-request');"
                    "if (b && window.did_handshake && window.socket?.connected) {b.click(); return true;}"
                    "return false;})()")
                if not began or not close.finished.wait(6):
                    warn("工作台尚未确认保存。请稍后重试。")
                    return
                if not close.saved:
                    return
            approved.set()
            try:
                window.destroy()
            finally:
                approved.clear()
        finally:
            pending.release()

    def closing():
        if approved.is_set():
            return True
        if pending.acquire(blocking=False):
            threading.Thread(target=flush_and_close, daemon=True).start()
        return False

    window.events.closing += closing


def main():
    if sys.platform == "darwin":
        from desktop import macos as native
    elif sys.platform == "win32":
        from desktop import windows as native
    else:
        raise SystemExit("桌面版支持 macOS 和 Windows。")

    try:
        profile = Path(os.environ.get("ACCEPTANCE_DESK_DESKTOP_PROFILE", native.default_profile())).expanduser().resolve()
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        with native.instance(profile) as primary:
            if not primary:
                return
            os.environ["NICEGUI_STORAGE_PATH"] = str(profile / "storage")
            os.environ.pop("NICEGUI_REDIS_URL", None)
            model_path = os.environ.get("ACCEPTANCE_DESK_BETA_MODEL")
            model = verify_model(Path(model_path).expanduser()) if model_path else None
            from desktop.server import WindowClose, serve
            import webview

            native.prepare()
            close = WindowClose()
            with serve(profile, model, close) as url:
                webview.settings["ALLOW_DOWNLOADS"] = True
                window = webview.create_window(NAME, url, width=1280, height=840, min_size=(720, 480),
                                               text_select=True)
                native.bind(window, profile)
                if sys.platform == "win32":
                    close.download = partial(native.save_download, window)
                save_before_close(window, close, lambda message: native.save_warning(window, message))
                webview.start(gui=native.GUI, private_mode=False, storage_path=str(profile / native.WEB_STORAGE))
    except (Exception, SystemExit) as exc:
        native.startup_error(exc)
