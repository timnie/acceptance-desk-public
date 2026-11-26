"""页面内的延迟保存和落盘反馈，复用 NiceGUI 的浏览器隔离存储。"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from pathlib import Path

from nicegui import run, ui

from .session import STORE_KEY, Session


class SaveStatus(Enum):
    SAVED = "已保存"
    PAGE_ONLY = "仅本页保存"
    SAVING = "保存中…"
    FAILED = "保存失败 · 重试"


class Autosave:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.status = SaveStatus.SAVED if session.save_enabled else SaveStatus.PAGE_ONLY
        self.dirty = False
        self.changed_at = 0.0
        self.confirm_revision: str | None = None
        self.deadline = 0.0
        self.timer = ui.timer(0.2, self._tick, active=False)

    @property
    def message(self) -> str:
        return self.status.value

    @property
    def confirmed(self) -> bool:
        """没有未提交的修改，当前保存或关闭存储的请求也已确认。"""
        return not self.dirty and self.status in (SaveStatus.SAVED, SaveStatus.PAGE_ONLY)

    async def wait_for_confirmation(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.confirm_revision is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return self.confirmed

    def changed(self) -> None:
        self.dirty = True
        self.changed_at = time.monotonic()
        self.status = SaveStatus.SAVING if self.session.save_enabled else SaveStatus.PAGE_ONLY
        self.timer.activate()

    def save(self) -> None:
        """显式保存和决定操作立即提交，输入操作由计时器合并。"""
        self.timer.deactivate()
        self.confirm_revision = None
        try:
            if self.session.save_enabled:
                self.session.persist()
            else:
                self.session.forget()
        except (OSError, ValueError, TypeError):
            self.dirty = True
            self.status = SaveStatus.FAILED
            return
        self.dirty = False
        self.status = SaveStatus.SAVING
        self.confirm_revision = self.session.saved_revision if self.session.save_enabled else ""
        self.deadline = time.monotonic() + 3
        self.timer.activate()

    async def _tick(self) -> None:
        if self.dirty:
            if time.monotonic() - self.changed_at >= 0.5:
                self.save()
            return
        revision = self.confirm_revision
        if revision is None:
            self.timer.deactivate()
            return
        path = getattr(self.session.storage, "filepath", None)
        try:
            saved = await run.io_bound(self._read_revision, Path(path)) if path is not None else self.confirm_revision
        except (OSError, ValueError):
            saved = None
        # 读盘期间可能又有输入或保存，旧检查不得覆盖新一轮反馈。
        if revision != self.confirm_revision or self.dirty:
            return
        if saved == revision:
            self.status = SaveStatus.SAVED if revision else SaveStatus.PAGE_ONLY
            self.confirm_revision = None
            self.timer.deactivate()
        elif time.monotonic() >= self.deadline:
            self.status = SaveStatus.FAILED
            self.confirm_revision = None
            self.timer.deactivate()

    @staticmethod
    def _read_revision(path: Path) -> str | None:
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        if not isinstance(stored, dict):
            raise ValueError("存档格式不正确")
        if STORE_KEY not in stored:
            return ""
        document = stored[STORE_KEY]
        return document.get("revision") if isinstance(document, dict) else None
