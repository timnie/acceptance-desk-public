"""工作台的合并预览和导出窗口。保存时机由打开窗口的页面决定。"""

import html

from nicegui import ui

from acceptance_desk import ReviewDataError, feedback_markdown, layout_differences, merge_decisions, review_record_markdown
from acceptance_desk.export import visible_whitespace
from acceptance_desk.feedback import SCOPES, SCOPE_LABELS
from review_ui import state

from .controls import button, disclosure, help_icon, segmented, tag
from .downloads import download_content
from .next_round import DEFAULT_REVISION_OPTIONS, LAYOUT_OPTIONS, PENDING_VERSIONS
from .session import Session


def _merge(sess: Session, review: dict, pending: str, layout: str):
    return merge_decisions(review, sess.original, sess.revised, pending=pending, layout=layout,
                           markdown="md" in sess.formats.values())


def show_export(sess: Session, review: dict, *, generate=None) -> ui.dialog:
    p = state.progress(review)
    pending = p["by_status"].get("pending", 0)
    choice = dict(DEFAULT_REVISION_OPTIONS)
    layout_diffs = layout_differences(review, sess.original, sess.revised, markdown="md" in sess.formats.values())

    def merged():
        try:
            policy = choice["policy"] if pending else "block"
            return _merge(sess, review, policy, choice["layout"])
        except ReviewDataError as exc:
            ui.notify(str(exc), type="negative")
            return None

    async def download_final() -> None:
        res = merged()
        if res is None:
            return
        ext = sess.formats.get("revised", "txt")
        name = state.export_names(sess.title, ext, res.draft, avoid=list(sess.names.values()))["final"]
        await download_content(res.text, name)

    async def download_record() -> None:
        res = merged()
        if res is None:
            return
        name = state.export_names(sess.title, sess.formats.get("revised", "txt"), res.draft)["record"]
        await download_content(review_record_markdown(review, res, title=sess.title, names=sess.names, formats=sess.formats), name)

    async def download_feedback() -> None:
        try:
            text = feedback_markdown(review, sess.original, sess.revised, title=sess.title, scope=choice["scope"],
                                     include_pending=choice["pending"])
        except ReviewDataError as e:
            ui.notify(str(e), type="negative")
            return
        await download_content(text, f"{sess.title}_修改意见.md", media_type="text/markdown; charset=utf-8")

    async def next_version() -> None:
        await generate(dict(choice), close_export=dialog.close)

    def choices(key: str, label: str, options: dict, *, help_text: str = "") -> None:
        def choose(value: str) -> None:
            choice[key] = value
            if key == "scope":
                scope_hint.set_text(SCOPES[value])

        with ui.element("div").classes("wb-choice-row"):
            with ui.element("div").classes("wb-help-line"):
                tag("span", label, "d")
                if help_text:
                    help_icon(label, help_text)
            segmented(options, choice[key], choose, label=label)

    with ui.dialog() as dialog, ui.element("div").classes("wb-dialog wb-export"):
        tag("h3", "导出")
        with ui.element("section").classes("wb-opt"):
            tag("div", "合并文稿", "h")
            if pending:
                tag("div", f"还有 {pending} 处待定，本次下载为草稿。", "d")
                choices("policy", "待定处采用", PENDING_VERSIONS)
            if layout_diffs:
                with disclosure(f"空白与换行（{len(layout_diffs)} 处）"):
                    choices("layout", "排版方式", LAYOUT_OPTIONS,
                            help_text="选「跟随决定」时，空白与换行随相邻的文字决定。也可以统一沿用其中一版。")
                    with disclosure("查看差异"):
                        for diff in layout_diffs:
                            tag("div", html.escape("、".join(diff.kinds)), "h")
                            tag("div", html.escape(f"原稿 {visible_whitespace(diff.original)} → 改稿 {visible_whitespace(diff.revised)}"), "wb-msg")
                            if diff.markdown_risk:
                                tag("div", html.escape(diff.markdown_risk), "wb-err")
            with ui.element("div").classes("wb-actions"):
                button("下载草稿" if pending else "下载终稿", download_final, "wb-btn primary")
        with ui.element("section").classes("wb-opt"):
            tag("div", "修改意见", "h")
            choices("scope", "修订范围", SCOPE_LABELS)
            scope_hint = ui.label(SCOPES[choice["scope"]]).classes("d").props("aria-live=polite")
            if pending:
                ui.checkbox("添加待定项", value=choice["pending"],
                            on_change=lambda e: choice.update(pending=e.value)).props("dense").classes("wb-export-pending")
            with ui.element("div").classes("wb-actions"):
                button("下载修改意见", download_feedback)
                if generate is not None:
                    button("按意见生成下一版", next_version)
        with ui.element("section").classes("wb-opt"):
            with ui.element("div").classes("wb-actions"):
                button("下载审阅记录", download_record, "wb-link")
                with ui.element("div").classes("wb-help-line"):
                    button("下载进度文件", lambda: download_content(
                        state.export_review(review), f"进度_{sess.title}.json", media_type="application/json"), "wb-link")
                    help_icon("进度文件", "保存本次的审阅决定。要换设备继续，导入同一对文稿，再到「进度与恢复」选择这份 .json。")
        with ui.element("div").classes("wb-actions wb-end"):
            button("完成", dialog.close)
    dialog.on("hide", dialog.delete, [])
    dialog.open()
    return dialog


def show_preview(sess: Session, review: dict) -> ui.dialog:
    pending = state.progress(review)["by_status"].get("pending", 0)
    policies = {"pending": "original", "layout": "follow"}

    @ui.refreshable
    def text() -> None:
        try:
            result = _merge(sess, review, policies["pending"], policies["layout"])
            tag("div", html.escape(result.text), "wb-preview-text wb-serif", trusted=False)
        except ReviewDataError as exc:
            tag("div", html.escape(str(exc)), "wb-err", trusted=False)

    def choose(key: str, value: str) -> None:
        policies[key] = value
        text.refresh()

    with ui.dialog() as dialog, ui.element("div").classes("wb-dialog wb-preview"):
        tag("h3", "合并结果预览")
        if sess.drafts:
            tag("div", f"还有 {len(sess.drafts)} 处编辑草稿，保存编辑后会进入合并结果。", "wb-msg")
        with ui.element("div").classes("wb-preview-options"):
            if pending:
                ui.select({"original": "待定处用原稿", "revised": "待定处用改稿"}, value="original",
                          label=f"还有 {pending} 处待定", on_change=lambda e: choose("pending", e.value)).props("outlined dense")
            ui.select(LAYOUT_OPTIONS, value="follow",
                      label="空白与换行", on_change=lambda e: choose("layout", e.value)).props("outlined dense")
        with ui.element("div").classes("wb-preview-body"):
            text()
        button("继续审阅", dialog.close)
    dialog.on("hide", dialog.delete, [])
    dialog.open()
    return dialog
