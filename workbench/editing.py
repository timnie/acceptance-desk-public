"""复用框架的事务编辑器；浏览器适配另由 Python 编译，不让撤销还原候选拼音。"""

import asyncio
from collections.abc import Callable

from nicegui import ui

from .keyboard import text_undo
from .text_commit import CommitGate


class EditorDialog(ui.element):
    """直接使用框架 QDialog，以 Python 调用其带选择器的公开 focus 方法。"""
    def __init__(self) -> None:
        super().__init__("q-dialog")
        self.props["model-value"] = False

    def open(self) -> None:
        self.props["model-value"] = True

    def close(self) -> None:
        if not self.is_deleted:
            self.props["model-value"] = False


class ProseEditor(ui.codemirror):
    def __init__(self, value: str = "", *, label: str, on_commit: Callable[[str], None] | None = None) -> None:
        self.gate = CommitGate(value)
        self.on_commit = on_commit
        self.generation = 0
        super().__init__(value, language=None, line_wrapping=True)
        self.classes("wb-prose w-full").props(f'role=group aria-label="{label}"')
        self.on_value_change(lambda event: self._publish(self.gate.update(event.value)))
        self.on("compositionstart", self._begin, [])
        self.on("compositionend", self._end, [])
        text_undo(self)  # 编辑器处理文字历史；不冒泡到审阅决定的撤销。

    def _publish(self, value: str | None) -> None:
        if value is not None and self.on_commit is not None:
            self.on_commit(value)

    def _begin(self) -> None:
        if self.gate.pending:
            self._publish(self.gate.flush())
        self.generation += 1
        self.gate.begin()

    def _end(self) -> None:
        self.generation += 1
        generation = self.generation
        self.gate.end()

        def finish() -> None:
            if generation == self.generation and not self.gate.composing:
                self.gate.raw = self.value
                self._publish(self.gate.flush())

        ui.timer(0.05, finish, once=True)

    async def settled_value(self) -> str:
        if self.gate.pending:
            await asyncio.sleep(0.06)  # 兼容 compositionend 后才到达的最后一条 input。
        if not self.gate.composing:
            self.gate.raw = self.value
            self._publish(self.gate.flush())
        return self.gate.committed
