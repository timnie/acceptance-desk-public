"""供工作台各个面板共用的原生控件。"""

import html
import json

from nicegui import ui


def tag(name: str, content: str = "", classes: str = "", *, trusted: bool = True):
    """一个原生标签。content 必须已转义；trusted=False 时再经浏览器端消毒（用于含用户文稿的片段）。"""
    return ui.html(content, tag=name, sanitize=not trusted).classes(classes)


def button(text: str, on_click, classes: str = "wb-btn"):
    return tag("button", html.escape(text), classes).on("click", on_click)


def segmented(options: dict, current, on_pick, classes: str = "wb-seg", *, label: str = "") -> None:
    """分段控件：一组互斥的选项，当前项高亮。options 是 {值: 文字}。"""
    buttons = {}

    def select(value) -> None:
        for key, item in buttons.items():
            if key == value:
                item.classes(add="on")
            else:
                item.classes(remove="on")
            item.props(f'aria-pressed={str(key == value).lower()}')
        on_pick(value)

    with ui.element("div").classes(classes).props("role=group") as group:
        if label:
            group.props["aria-label"] = label
        for value, text in options.items():
            buttons[value] = tag("button", text, "on" if value == current else "").props(
                f'aria-pressed={str(value == current).lower()}').on("click", lambda value=value: select(value))


def disclosure(label: str, classes: str = ""):
    """原生展开控件负责键盘操作、展开状态与读屏反馈。"""
    with ui.element("details").classes(f"wb-disclosure {classes}"):
        tag("summary", html.escape(label))
        return ui.element("div").classes("wb-disclosure-body")


def help_icon(label: str, description: str) -> None:
    with ui.element("button").classes("wb-help").props(f'type=button aria-label="{label}说明"') as control:
        tag("span", "?").props("aria-hidden=true")
        tooltip = ui.tooltip(description).classes("wb-help-tooltip").props(
            'anchor="bottom middle" self="top middle" :offset="[0,6]" transition-show=fade transition-hide=fade :delay=180')
    control.on("click", lambda: tooltip.run_method("show"))


def focus_in(container, selector: str, *, scroll: bool = False) -> None:
    """在刷新后的 DOM 中定位控件。"""
    if container.is_deleted:
        return
    container.client.run_javascript(f"""
        setTimeout(() => {{
            const control = getHtmlElement({container.id})?.querySelector({json.dumps(selector)});
            if (!control?.getClientRects().length || control.disabled) return;
            control.focus({{preventScroll: true}});
            if ({str(scroll).lower()}) control.scrollIntoView({{block: 'nearest', inline: 'nearest'}});
        }});
    """)


def dialog_focus(dialog, container, selector: str) -> None:
    """关闭后把焦点还给打开弹窗的控件，即使控件已经重绘。"""
    dialog.on("hide", lambda: focus_in(container, selector), [])


class SearchPicker(ui.element):
    """在所属面板内显示搜索结果，使用原生按钮完成选择。"""

    def __init__(self, options: dict, value: str, label: str, on_pick=None) -> None:
        super().__init__("details")
        self.options, self.value = options, value
        self.label = label
        self.on_pick = on_pick
        self.classes("wb-picker")
        with self:
            self.summary = tag("summary", self._title())
            self.search = ui.input(f"搜索{label}", on_change=lambda: self.rows.refresh()).props(
                "dense outlined clearable").classes("w-full")
            with ui.element("div").classes("wb-picker-results").props(f'aria-label="{label}"'):
                self.rows()

    def _title(self) -> str:
        current = html.escape(self.options.get(self.value, self.value))
        return f'<span>{html.escape(self.label)}</span><strong>{current}</strong>'

    @ui.refreshable_method
    def rows(self) -> None:
        query = (self.search.value or "").casefold().strip()
        matches = [(key, text) for key, text in self.options.items()
                   if query in key.casefold() or query in text.casefold()]
        for key, text in matches[:120]:
            button(text, lambda key=key: self._pick(key), "wb-picker-option").props(
                f'aria-pressed={str(self.value == key).lower()}')
        if not matches:
            tag("p", "没有匹配项", "wb-msg")
        if len(matches) > 120:
            tag("p", "输入更多字符以缩小范围", "wb-msg")

    def _pick(self, value: str) -> None:
        self.set_options(self.options, value=value)
        self.run_method("removeAttribute", "open")
        self.client.run_javascript(f"getHtmlElement({self.summary.id})?.focus()")
        if self.on_pick:
            self.on_pick(value)

    def set_options(self, options: dict, *, value: str) -> None:
        self.options, self.value = options, value
        self.summary.set_content(self._title())
        self.rows.refresh()

    def disable(self) -> None:
        self.props("inert")

    def enable(self) -> None:
        self.props(remove="inert")
