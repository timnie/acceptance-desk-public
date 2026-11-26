"""Serve one local desktop window using the workbench's existing NiceGUI pages."""
import hashlib
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from nicegui import ui
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.middleware.sessions import SessionMiddleware

from workbench import app as workbench


class WindowClose:
    def __init__(self):
        self.finished = threading.Event()
        self.saved = False
        self.download = None

    def bind(self):
        client = ui.context.client
        client.desktop_download = self.download

        async def flush():
            self.saved = False
            try:
                autosave = getattr(client, "desktop_autosave", None)
                if autosave is not None:
                    from workbench.editing import ProseEditor
                    for element in list(client.elements.values()):
                        if isinstance(element, ProseEditor) and not element.is_deleted:
                            await element.settled_value()
                    if autosave.dirty or (not autosave.confirmed and autosave.confirm_revision is None):
                        autosave.save()
                    if not await autosave.wait_for_confirmation(timeout=4):
                        ui.notify("文稿未能写入磁盘。请检查存储空间或权限，再次退出。", type="negative")
                        return
                self.saved = True
            finally:
                self.finished.set()

        ui.element("button").props("type=button").classes("desktop-close-request").style("display:none").on("click", flush, [])


@contextmanager
def serve(profile: Path, model: Path | None, close: WindowClose):
    workbench.load_beta_model(model)
    secret = workbench._storage_secret(profile / "session-key")
    identity = hashlib.sha256(("desktop:" + secret).encode()).hexdigest()
    token = secrets.token_urlsafe(32)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/open/{ticket}")
    async def open_window(ticket: str, request: Request):
        nonlocal token
        if not token or not secrets.compare_digest(ticket, token):
            return Response(status_code=403)
        token = ""
        request.session["id"] = identity
        return RedirectResponse("/", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    def desktop_session(inner: ASGIApp) -> ASGIApp:
        async def check(scope: Scope, receive: Receive, send: Send):
            if (scope["type"] in {"http", "websocket"} and not scope["path"].startswith("/open/")
                    and scope["session"].get("id") != identity):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await Response(status_code=403)(scope, receive, send)
                return
            await inner(scope, receive, send)
        return check

    workbench.desktop_page_setup = close.bind
    app.add_middleware(desktop_session)
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="acceptance_desk_desktop")
    ui.run_with(app, title="AI 改稿验收台", language="zh-CN", dark=None, show_welcome_message=False)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/open/{token}"
        config = uvicorn.Config(app, ws="wsproto", loop="asyncio", access_log=False, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("工作台本机服务未能启动。")
                time.sleep(0.03)
            yield url
        finally:
            server.should_exit = True
            thread.join(8)
            workbench.desktop_page_setup = None
