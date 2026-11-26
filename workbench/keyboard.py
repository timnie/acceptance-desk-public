"""框架事件修饰符接线快捷键，文字历史由各输入区自行处理。"""

from __future__ import annotations

from collections.abc import Callable


def shortcuts(element, *, save: Callable, undo: Callable | None = None, redo: Callable | None = None,
              previous: Callable | None = None, next_change: Callable | None = None) -> None:
    def invoke(callback, event):
        if not event.args.get("repeat") and not event.args.get("isComposing"):
            return callback()

    actions = {"s": save}
    if undo is not None:
        actions["z"] = undo
    if redo is not None:
        actions["shift.z"] = redo
    for modifier in ("ctrl", "meta"):
        for key, callback in actions.items():
            element.on(f"keydown.{modifier}.{key}.exact.prevent.stop",
                       lambda event, callback=callback: invoke(callback, event), ["repeat", "isComposing"])
    if redo is not None:
        element.on("keydown.ctrl.y.exact.prevent.stop", lambda event: invoke(redo, event), ["repeat", "isComposing"])
    for key, callback in (("up", previous), ("down", next_change)):
        if callback is not None:
            element.on(f"keydown.alt.{key}.exact.prevent.stop",
                       lambda event, callback=callback: invoke(callback, event), ["repeat", "isComposing"])


def text_undo(element) -> None:
    for modifier, key in (("ctrl", "z"), ("meta", "z"), ("ctrl", "y")):
        element.on(f"keydown.{modifier}.{key}.stop", lambda: None, [])
    # 光标移动由文本框处理，避免触发工作台上一处/下一处。
    element.on("keydown.alt.up.stop", lambda: None, [])
    element.on("keydown.alt.down.stop", lambda: None, [])
