"""首次使用浮窗与贴在实际控件旁的逐步引导。"""

from __future__ import annotations

import html
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Literal

from nicegui import ui
from nicegui.element import Element


GUIDE_KEY = "workbench_guide"


@dataclass(frozen=True)
class GuideStep:
    target: Element
    title: str
    description: str
    placement: Literal["below", "above", "inside"] = "below"
    align: str = "left"
    anchor: Element | None = None


class Guide:
    def __init__(self, storage: MutableMapping) -> None:
        self.storage = storage
        self.steps: list[GuideStep] = []
        self.notes: list[ui.menu] = []
        self.current: int | None = None
        self.opener: Element | None = None
        self.welcome: ui.dialog | None = None
        self.importing = False
        self.can_generate = False

    def mount(self, steps: list[GuideStep], opener: Element, *, importing: bool = False,
              can_generate: bool = False) -> bool:
        """当前页面重建后绑定新的控件。返回是否需要给引导保留焦点。"""
        self.opener, self.importing = opener, importing
        self.current = None
        self.can_generate = can_generate
        self.steps = steps
        self.notes = [self._note(step, index) for index, step in enumerate(steps)]
        self.welcome = self._welcome()
        if GUIDE_KEY not in self.storage:
            self.storage[GUIDE_KEY] = "seen"
            self.open()
            return True
        return False

    def start(self) -> None:
        self.storage[GUIDE_KEY] = "seen"
        self._show(0)

    def open(self) -> None:
        self._close_note()
        if self.welcome is not None:
            self.welcome.open()

    def _welcome(self) -> ui.dialog:
        begin = False

        def accept() -> None:
            nonlocal begin
            begin = True
            dialog.close()

        def closed() -> None:
            nonlocal begin
            if begin:
                begin = False
                self.start()
            else:
                self.dismiss()

        with ui.dialog().props('aria-label="新手教程"') as dialog:
            with ui.element("div").classes("wb-dialog wb-guide-welcome"):
                ui.html("欢迎使用验收台", tag="h2")
                ui.html("导入两版文稿，逐处决定采用哪一版，再导出终稿和写给模型的修改意见。", tag="p")
                if self.can_generate:
                    ui.html("审阅后可在「生成新稿」里连接已登录的本机 CLI，或填写 API Key。", tag="p")
                with ui.element("div").classes("wb-actions wb-end"):
                    self._button("跳过", dialog.close)
                    self._button("开始引导", accept, primary=True).props("data-autofocus")
        dialog.on("hide", closed, [])
        return dialog

    def _note(self, step: GuideStep, index: int) -> ui.menu:
        heading, edge, x, y = {"below": ("bottom", "top", 0, 8), "above": ("top", "bottom", 0, 8),
                               "inside": ("top", "top", -12, -48)}[step.placement]
        anchor = step.anchor if step.anchor is not None else step.target
        with ui.menu().classes("wb-guide-note").props(
            f'no-parent-event persistent no-refocus role=dialog aria-label="{step.title}" '
            f'target="#{anchor.html_id}" anchor="{heading} {step.align}" self="{edge} {step.align}" '
            f':offset="[{x},{y}]" :transition-duration=0'
        ) as note:
            note.on("keydown.esc.prevent.stop", self.dismiss, [])
            with ui.element("div").classes("wb-guide-note-head"):
                ui.html(html.escape(step.title), tag="h2")
                ui.button(icon="close", color=None, on_click=self.dismiss).classes("wb-guide-close").props(
                    'flat dense aria-label="结束引导"')
            ui.html(html.escape(step.description), tag="p")
            with ui.element("div").classes("wb-guide-note-footer"):
                ui.label(f"{index + 1} / {len(self.steps)}").classes("wb-num").props(
                    f'aria-label="第 {index + 1} 步，共 {len(self.steps)} 步"')
                advance = self._button("完成" if index + 1 == len(self.steps) else "下一步",
                                       lambda: self._advance(index), primary=True).props("data-autofocus")
            note.on("show", lambda: advance.run_method("focus", {"preventScroll": True}), [])
        return note

    @staticmethod
    def _button(label: str, action, *, primary: bool = False) -> Element:
        with ui.element("button").props("type=button").classes("wb-btn primary" if primary else "wb-btn") as control:
            ui.html(html.escape(label), tag="span")
        return control.on("click", action)

    def _show(self, index: int) -> None:
        self._close_note()
        self.current = index
        target = self.steps[index].target
        target.classes(add="wb-guide-target")
        if self.importing:
            target.run_method("scrollIntoView", {"block": "nearest", "inline": "nearest"})
        self.notes[index].open()

    def _advance(self, index: int) -> None:
        if self.current != index:
            return
        if index + 1 < len(self.steps):
            self._show(index + 1)
        else:
            self._close_note()
            self._focus_opener()

    def dismiss(self) -> None:
        self.storage[GUIDE_KEY] = "seen"
        self._close_note()
        self._focus_opener()

    def _close_note(self) -> None:
        if self.current is not None:
            self.notes[self.current].close()
            self.steps[self.current].target.classes(remove="wb-guide-target")
            self.current = None

    def _focus_opener(self) -> None:
        if self.opener is not None and not self.opener.is_deleted:
            self.opener.run_method("focus", {"preventScroll": True})
