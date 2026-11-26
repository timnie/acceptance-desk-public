"""文稿工作台界面（NiceGUI）。

三栏：左侧修改目录，中间连续正文加页边批注，右侧当前修改的依据和决定。正文里的每处修改都可以点。
进度按浏览器保存在本机服务端的 NiceGUI 用户存储里。作者可显式选择模型连接并确认发送。
"""

from __future__ import annotations

import html
import hashlib
import os
import secrets
import warnings
from collections.abc import Iterable
from copy import copy
from pathlib import Path

from nicegui import app, run, ui

from acceptance_desk import MergeError, PendingDecisions, ReviewDataError, audit, original_hints
from review_ui import render, state

from . import view
from acceptance_desk.feedback import SCOPE_LABELS

from .controls import SearchPicker, button, dialog_focus, disclosure, focus_in, help_icon, segmented, tag
from .export_dialogs import show_export, show_preview
from .document_pane import DocumentPane
from .editing import EditorDialog, ProseEditor
from .guidance import Guide, GuideStep
from .model_connection import loopback
from .model_dialogs import ModelDialogs, article
from .next_round import DEFAULT_REVISION_OPTIONS, LAYOUT_OPTIONS, RevisionRequest
from .keyboard import shortcuts, text_undo
from .saving import Autosave
from .session import MODEL_FILTER, STORE_KEY, Session
from acceptance_desk.rule_classifier import RuleClassifier

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples" if (ROOT / "examples").is_dir() else ROOT.parent / "examples"
STYLE = ROOT / "style.css"
_beta_model: RuleClassifier | None = None
desktop_page_setup = None
PROSE_ASSETS = ROOT / "assets" / "prose"
PROSE_URL = "/_workbench/prose/" + hashlib.sha256((PROSE_ASSETS / "manifest.json").read_bytes()).hexdigest()[:12]
app.add_static_files(PROSE_URL, PROSE_ASSETS)
APPEARANCE_KEY = "workbench_appearance"
APPEARANCES = {"system": None, "light": False, "dark": True}
FILTER_ZH = {"all": "全部", "pending": "待处理", "flagged": "检查提示", "other": "无提示"}
MODEL_NAME = "审阅雷达"
MODEL_FILTER_ZH = "模型优先"
EXAMPLE_SETS = {"long": ("long_", "社区图书馆使用与阅读习惯（虚构示例）"), "short": ("", "线上讨论与课程成绩（虚构示例）")}
# Microsoft Fluent System Icons: https://github.com/microsoft/fluentui-system-icons
# MIT license in assets/fluent-LICENSE; the original vectors remain unmodified on disk.
HISTORY_ICONS = {name: (ROOT / "assets" / f"arrow-{name}.svg").read_text(encoding="utf-8").replace(
    'fill="#212121"', 'fill="currentColor"').replace('<svg ', '<svg aria-hidden="true" focusable="false" ')
    for name in ("undo", "redo")}


def _audit(original: str, revised: str) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)     # 模型不可用时退回规则模式，界面上另有说明
        return audit(original, revised)


def menu_action(label: str, on_click, shortcut: str = ""):
    with ui.element("button").classes("wb-menu-row").props("type=button role=menuitem") as item:
        tag("span", "", "wb-menu-check").props("aria-hidden=true")
        tag("span", html.escape(label), "wb-menu-label")
        if shortcut:
            tag("kbd", html.escape(shortcut), "wb-menu-key")
    item.on("click", on_click)
    menu = next(parent for parent in item.ancestors() if isinstance(parent, ui.menu))
    item.on("click", menu.close)
    return item


def menu_toggle(label: str, value: bool, on_change):
    """整行可点击的菜单开关；小勾选标记与读屏状态同时更新。"""
    with ui.element("button").classes("wb-menu-row").props(
            f'type=button role=menuitemcheckbox aria-checked={str(value).lower()}') as item:
        mark = tag("span", "✓" if value else "", "wb-menu-check").props("aria-hidden=true")
        tag("span", html.escape(label), "wb-menu-label")

    def toggle() -> None:
        nonlocal value
        value = not value
        mark.set_content("✓" if value else "")
        item.props(f'aria-checked={str(value).lower()}')
        on_change(value)

    item.on("click", toggle)
    return item


def menu_navigation(menu, items: list) -> None:
    """使用原生按钮保留 Enter/空格激活，补齐菜单方向键与首尾跳转。"""
    current = -1

    def remember(index: int) -> None:
        nonlocal current
        current = index

    def focus(index: int) -> None:
        remember(index % len(items))
        items[current].run_method("focus")

    menu.on("show", lambda: remember(-1))
    for index, item in enumerate(items):
        item.on("focus", lambda index=index: remember(index))
    menu.on("keydown.down.prevent.stop", lambda: focus(current + 1))
    menu.on("keydown.up.prevent.stop", lambda: focus(current - 1 if current >= 0 else -1))
    menu.on("keydown.home.prevent.stop", lambda: focus(0))
    menu.on("keydown.end.prevent.stop", lambda: focus(-1))


def appearance_menu(dark: ui.dark_mode):
    """外观独立于文稿进度，自动模式由框架跟随系统。"""
    with ui.button(icon="contrast", color=None).props("flat dense aria-label=外观").classes("wb-appearance"):
        with ui.menu().classes("wb-appearance-menu").props("transition-show=fade transition-hide=fade"):
            tag("div", "外观", "wb-menu-title")
            return ui.radio({"system": "跟随系统", "light": "浅色", "dark": "深色"}).props("dense size=xs").bind_value(
                dark, forward=lambda value: APPEARANCES[value],
                backward=lambda value: next(key for key, mode in APPEARANCES.items() if mode is value))


def tip(text: str, help_text: str) -> str:
    """带悬停说明的文字：说明只在需要时出现，不占版面。两段文字都必须已转义。"""
    return f'<span class="wb-tip" title="{help_text}">{text}</span>'



# ---------------------------------------------------------------- 载入页

def file_picker(label: str, accept: str, on_upload, on_error, *, rejected: str) -> None:
    """整颗按钮都能打开文件选择器；已读入的文件名由调用处显示。"""
    upload = ui.upload(on_upload=on_upload, auto_upload=True, max_files=1,
                       on_rejected=lambda: on_error(rejected))
    upload.props(f"flat accept={accept}").classes("wb-file-picker")
    upload.add_slot("header", f'''
        <q-btn v-if="props.isUploading" flat no-caps class="wb-file-button"
            label="取消上传" @click="props.abort" />
        <q-btn v-else flat no-caps class="wb-file-button" icon="attach_file"
            label="{label}" @click="props.pickFiles">
            <q-uploader-add-trigger />
        </q-btn>
    ''')
    upload.add_slot("list", '<template></template>')
    upload.on("dragenter", js_handler="e => e.currentTarget.classList.add('wb-drag-over')")
    upload.on("dragleave", js_handler="e => { if (!e.currentTarget.contains(e.relatedTarget)) e.currentTarget.classList.remove('wb-drag-over') }")
    upload.on("drop", js_handler="e => e.currentTarget.classList.remove('wb-drag-over')")
    upload.on("finish", upload.reset, [])
    upload.on("failed", lambda: on_error(f"{label}时上传失败，请重试。"), [])


def import_view(sess: Session, enter, dark: ui.dark_mode | None = None, *, guide: Guide | None = None,
                models: ModelDialogs | None = None, resume=None,
                saved_title: str | None = None, saved_at: str = "") -> None:
    fields: dict[str, ProseEditor] = {}
    names: dict[str, str | None] = {"original": None, "revised": None}
    format_fields: dict[str, ui.select] = {}
    file_labels: dict[str, ui.label] = {}
    counts: dict[str, ui.label] = {}
    progress_file: bytes | None = None
    loading = False

    def show_error(message: str) -> None:
        error.set_content(html.escape(message))

    def progress_error(message: str) -> None:
        progress_feedback.set_content(html.escape(message))
        if message:
            options_target.run_method("setAttribute", "open", "")

    def show_file_name(label: ui.label, name: str) -> None:
        label.set_text(name)
        label.props["title"] = name

    def show_count(side: str, value: str) -> None:
        count = sum(not ch.isspace() for ch in value)
        counts[side].set_text(f"{count:,} 字")

    async def start(original: str, revised: str, title: str, file_names: dict, formats: dict) -> None:
        nonlocal loading
        if loading:
            return
        saved = progress_file
        missing = "和".join(label for text, label in ((original, "原稿"), (revised, "改稿")) if not text.strip())
        if missing:
            if saved is not None:
                progress_error(f"请先放入保存进度时的{missing}，再恢复审阅。")
            else:
                show_error(f"请先放入{missing}。")
            return
        show_error("")
        progress_error("")
        loading = True
        try:
            if sess.storage.get(STORE_KEY) and not await confirm_replacement():
                return
            note = ui.notification("正在核对进度文件与文稿……" if saved is not None else "正在检查两份文稿……",
                                   spinner=True, timeout=None)
            try:
                report = await run.io_bound(_audit, original, revised)
            finally:
                note.dismiss()
        finally:
            loading = False
        if start_button.is_deleted:
            return
        try:
            message = sess.load(original, revised, report, title=title, names=file_names, formats=formats,
                                saved_review=saved)
        except (state.ReviewError, ReviewDataError) as exc:
            if saved is not None:
                progress_error(str(exc))
            else:
                show_error(str(exc))
            return
        sess.rounds = []
        sess.save_enabled = remember.value
        if not sess.save_enabled:
            sess.forget()
        sess.persist()
        enter(message)

    async def confirm_replacement() -> bool:
        with ui.dialog().props('aria-label="开始新的审阅"') as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", "开始新的审阅？")
            tag("p", "本机已有保存的文稿。开始新审阅会替换这份存档及其历史轮次。"
                     "可以先继续上次审阅，导出需要的记录。")
            with ui.element("div").classes("wb-actions wb-end"):
                button("返回", lambda: dialog.submit(False)).props("data-autofocus")
                button("替换并开始", lambda: dialog.submit(True), "wb-btn primary")
        try:
            return bool(await dialog)
        finally:
            dialog.delete()

    async def on_upload(side: str, event) -> None:
        try:
            text, fmt = state.read_upload(await event.file.read(), event.file.name, "原稿" if side == "original" else "改稿")
        except state.ReviewError as e:
            show_error(str(e))
            return
        fields[side].value = text
        show_count(side, text)
        names[side] = event.file.name
        format_fields[side].value = fmt
        show_file_name(file_labels[side], event.file.name)
        show_error("")

    async def on_progress_upload(event) -> None:
        nonlocal progress_file
        try:
            data = await event.file.read()
            state.parse_progress_file(data)
        except state.ReviewError as exc:
            progress_error(str(exc))
            return
        progress_file = data
        show_file_name(progress_label, event.file.name)
        clear_progress_button.set_visibility(True)
        show_error("")
        progress_error("")
        start_button.set_content("恢复审阅")

    def clear_progress() -> None:
        nonlocal progress_file
        progress_file = None
        show_file_name(progress_label, "")
        clear_progress_button.set_visibility(False)
        progress_error("")
        start_button.set_content("开始检查")

    async def start_typed() -> None:
        original = await fields["original"].settled_value()
        revised = await fields["revised"].settled_value()
        title = Path(names["revised"]).stem if names["revised"] else "粘贴的文稿"
        formats = {side: field.value for side, field in format_fields.items()}
        await start(original, revised, title, dict(names), formats)

    async def start_example(kind: str) -> None:
        prefix, title = EXAMPLE_SETS[kind]
        texts = {side: (EXAMPLES / f"{prefix}{side}.txt").read_text(encoding="utf-8") for side in ("original", "revised")}
        if progress_file is not None:
            progress_error("已选择进度文件。清除后可打开示例，或放入对应文稿来恢复审阅。")
            return
        await start(texts["original"], texts["revised"], title, {side: f"{prefix}{side}.txt" for side in texts},
                    dict.fromkeys(texts, "txt"))

    with ui.element("main").classes("wb-import"):
        with ui.element("header").classes("wb-import-head"):
            with ui.element("div").classes("wb-import-title"):
                ui.icon("edit_note").classes("wb-app-icon").props("aria-hidden=true")
                tag("h1", "导入文稿")
            with ui.element("div").classes("wb-import-tools"):
                if sess.beta_model is not None:
                    def import_radar() -> None:
                        with ui.dialog().props('aria-label="审阅雷达"') as dialog, ui.element("div").classes("wb-dialog"):
                            tag("h3", "审阅雷达")
                            tag("p", "项目自训模型已加载。两版稿件准备好后，开始检查即可查看审阅建议。")
                            button("关闭", dialog.close)
                        dialog.on("hide", dialog.delete, [])
                        dialog.open()
                    button("审阅雷达", import_radar, "wb-btn wb-radar").props("aria-haspopup=dialog")
                if models is not None:
                    button("模型连接", lambda: models.settings(), "wb-btn wb-guide-entry").props("aria-haspopup=dialog")
                if guide is not None:
                    with ui.element("button").classes("wb-btn wb-guide-entry").props(
                            'type=button aria-haspopup=dialog') as guide_button:
                        tag("span", "新手教程")
                    guide_button.on("click", guide.open)
                if dark is not None:
                    appearance_menu(dark)
        with ui.element("div").classes("wb-import-formats"):
            tag("p", "可粘贴文字，或导入纯文本（.txt）和 Markdown（.md）。")
            with disclosure("Word、PDF 怎么导入？", "wb-conversion"):
                tag("p", "在 Word 里选「文件 → 另存为 → 纯文本（.txt）」，编码选择 UTF-8。PDF 中能选中的文字可以直接复制到输入框。")
                tag("p", "也可以用微软的 MarkItDown 把 Word（.docx）或 PDF 转成 .md，再选择生成的文件。旧版 .doc 请先用 Word 另存为 .docx。")
                tag("code", 'markitdown "原稿.pdf" -o "原稿.md"')
                tag("p", "扫描版 PDF 需要先做文字识别。")
                ui.link("打开 MarkItDown 安装与使用说明", "https://github.com/microsoft/markitdown#installation",
                        new_tab=True).classes("wb-link").props("rel=noopener")
        if resume is not None:
            with ui.element("section").classes("wb-resume"):
                with ui.element("div"):
                    ui.label(saved_title or "本机保存的文稿").classes("wb-resume-title")
                    if isinstance(saved_at, str) and saved_at:
                        ui.label(f"最近保存 {saved_at}").classes("wb-msg")
                async def resume_click() -> None:
                    nonlocal loading
                    if loading:
                        return
                    loading = True
                    resume_button.props("disabled")
                    try:
                        await resume()
                    finally:
                        loading = False
                        if not resume_button.is_deleted:
                            resume_button.props(remove="disabled")
                resume_button = button("继续上次审阅", resume_click)
        with ui.element("div").classes("wb-cols"):
            for side, zh in (("original", "原稿"), ("revised", "改稿")):
                with ui.element("div").classes("wb-field"):
                    with ui.element("div").classes("wb-field-head") as field_head:
                        if side == "original":
                            fields_anchor = field_head
                        with ui.element("div").classes("wb-field-title"):
                            ui.icon("description" if side == "original" else "edit_document").props("aria-hidden=true")
                            tag("h2", zh)
                        format_fields[side] = ui.select(state.FORMATS, value="txt").props(
                            f'dense borderless options-dense aria-label="{zh}格式"')
                    fields[side] = ProseEditor(label=zh, on_commit=lambda text, side=side: show_count(side, text)).classes("wb-paste")
                    with ui.element("div").classes("wb-file-row"):
                        file_picker(f"拖入或选择{zh}", ".txt,.md,.markdown", lambda e, s=side: on_upload(s, e), show_error,
                                    rejected="请选择纯文本或 Markdown 文件。Word 和 PDF 请按上方指引转换。")
                        file_labels[side] = ui.label("可直接粘贴正文").classes("wb-file-name").props("aria-live=polite")
                        counts[side] = ui.label("0 字").classes("wb-file-count")
        error = tag("div", "", "wb-err").props("role=alert")
        with ui.element("div").classes("wb-import-footer"):
            options = disclosure("进度与恢复", "wb-import-options")
            options_target = next(options.ancestors())
            with options:
                tag("p", "在工作台用「导出 → 下载进度文件」保存审阅决定。恢复时放入原来的两版文稿，选择该文件，再点「恢复审阅」。")
                with ui.element("div").classes("wb-file-row"):
                    file_picker("选择进度文件（.json）", ".json", on_progress_upload, progress_error,
                                rejected="请选择验收台导出的 .json 进度文件。")
                    progress_label = ui.label().classes("wb-file-name").props("aria-live=polite")
                    clear_progress_button = button("清除", clear_progress, "wb-btn")
                    clear_progress_button.set_visibility(False)
                progress_feedback = tag("div", "", "wb-err").props("role=alert")
                with ui.element("div").classes("wb-help-line"):
                    remember = ui.checkbox("在这台机器保存进度", value=sess.save_enabled)
                    help_icon("本机保存", "勾选后，刷新或用这个浏览器重开时可以继续审阅。换设备时请下载进度文件。")
            with ui.element("div").classes("wb-import-actions") as start_anchor:
                with ui.element("div").classes("wb-examples").props("role=group aria-label=示例文稿"):
                    with disclosure("打开示例", "wb-example-menu"):
                        button("短文示例", lambda: start_example("short"))
                        button("长文示例", lambda: start_example("long"))
                start_button = button("开始检查", start_typed, "wb-btn primary")
        if guide is not None:
            recovery = "有本机保存的文稿时，可以点「继续上次审阅」。也可在这里选下载的进度文件，并放回原来的两版文稿。"
            guide.mount([
                GuideStep(fields_anchor, "放入两版文稿", "把原稿和改稿分别粘贴到对应的位置，也可以选择文本文件。"),
                GuideStep(options_target, "恢复审阅进度", recovery, placement="above"),
                GuideStep(start_anchor, "开始检查或先试用", "两版稿件准备好后，选择「开始检查」。想先练习，可以打开「短文示例」。", placement="above", align="right"),
            ], guide_button, importing=True, can_generate=models is not None)


# ---------------------------------------------------------------- 工作台

def workbench_view(sess: Session, leave, message: str | None = None, dark: ui.dark_mode | None = None, *,
                   guide: Guide | None = None, models: ModelDialogs | None = None, rerender=None,
                   switch_round=None, viewed_round: int | None = None) -> None:
    review = sess.review
    historical = viewed_round is not None
    round_index = viewed_round if historical else len(sess.rounds)
    filter_labels = dict(FILTER_ZH)
    radar_saturated = sess.radar_saturated
    if sess.reference_suggestions:
        filter_labels[MODEL_FILTER] = "出处待核对"
    elif sess.model_suggestions is not None and not radar_saturated:
        filter_labels[MODEL_FILTER] = MODEL_FILTER_ZH
    if sess.flt not in filter_labels:
        sess.flt = "all"
    saving = Autosave(sess)
    ui.context.client.desktop_autosave = None if historical else saving
    note_editor: ProseEditor | None = None

    async def save_progress() -> None:
        if historical:
            return
        if note_editor is not None:
            await note_editor.settled_value()
        saving.save()
    try:
        is_mac = "Mac" in ui.context.client.request.headers.get("user-agent", "")
    except RuntimeError:  # 无请求的预构造（例如界面检查）使用 Ctrl 标记。
        is_mac = False
    keys = {"undo": "⌘Z" if is_mac else "Ctrl+Z", "redo": "⇧⌘Z" if is_mac else "Ctrl+Y",
            "save": "⌘S" if is_mac else "Ctrl+S"}
    note_sessions: set[str] = set()
    navigation_open = False
    nav_rows: dict[str, ui.html] = {}
    try:        # 原稿存量提示由核心给出；它只是词表命中，不计入进度，算不出来也不影响审阅
        hint_list = original_hints(review, sess.original, sess.revised)
    except ReviewDataError:
        hint_list = []
    hints = view.hints_by_segment(sess.segments, sess.original, hint_list)

    # ------------------------------ 状态变更

    def toggle_navigation(opened: bool) -> None:
        nonlocal navigation_open
        was_open, navigation_open = navigation_open, opened
        main_panel.classes(add="wb-nav-open") if opened else main_panel.classes(remove="wb-nav-open")
        navigation_button.props(f'aria-expanded={str(opened).lower()}')
        if opened:
            focus_in(main_panel, ".wb-search input")
        elif was_open:
            focus_in(shell, ".wb-nav-toggle")

    def select(cid: str | None, *, scroll: bool = False, close_navigation: bool = True,
               changed: tuple[str, ...] | None = None, refilter: bool = False, focus: str = "") -> None:
        if cid is None:
            return
        if close_navigation:
            toggle_navigation(False)
        previous, sess.cur = sess.cur, cid
        repaint = (*(changed or ()), previous, cid)
        document.repaint(repaint)
        if changed is not None:
            refresh_review_controls(repaint)
        else:
            update_navigation(repaint, refilter=refilter)
            side.refresh()
        saving.save()
        if scroll:
            document.scroll_to(cid)
            shell.client.run_javascript(f"""
                setTimeout(() => {{
                    const row = getHtmlElement({navigation.id})?.querySelector('.wb-row[aria-current=true]');
                    if (row?.getClientRects().length) row.scrollIntoView({{block: 'nearest', inline: 'nearest'}});
                }});
            """)
        if focus:
            focus_in(decision_anchor, focus, scroll=True)

    document = DocumentPane(sess, hints, select)

    def after_change(ids: list[str]) -> None:
        saving.save()
        document.repaint(ids)
        refresh_review_controls(ids)

    @ui.refreshable
    def pending_map() -> None:
        changes = review["changes"]
        if sess.display == "drafts" or len(changes) < 12:
            return
        bands = {}
        width = max(len(sess.original), len(sess.revised), 1)
        for change in changes:
            if change["decision"]["status"] != "pending":
                continue
            position = change["original"].get("start")
            if position is None:
                position = change["revised"].get("start") or 0
            band = min(19, max(0, position * 20 // width))
            bands.setdefault(band, []).append(change["id"])
        if bands:
            with ui.element("nav").classes("wb-pending-map").props('aria-label="文稿待定修改分布"'):
                for band, ids in bands.items():
                    label = f"定位第 {view.number(ids[0])} 处"
                    if len(ids) > 1:
                        label += f"（附近还有 {len(ids) - 1} 处待定）"
                    button("", lambda cid=ids[0]: reveal(cid),
                           "wb-pending-mark").props(f'aria-label="{label}" title="{label}"').style(f"top:{band * 5}%")

    def refresh_review_controls(ids: Iterable[str | None] | None = None) -> None:
        progress.refresh()
        update_navigation(ids, refilter=True)
        side.refresh()
        history_controls.refresh()
        pending_map.refresh()

    def decide(cid: str, status: str, text: str | None = None) -> None:
        sess.decide(cid, status, text)
        note_sessions.discard(cid)
        nxt = None
        if status != "pending" and sess.auto_advance:
            pending = state.batch_targets(review, sess.visible())
            nxt = state.step(sess.order, pending, cid, +1)
        focus = {"accepted": ".wb-accept", "rejected": ".wb-reject", "pending": ".wb-decision-title"}
        select(nxt or cid, scroll=bool(nxt), changed=(cid,), focus=focus.get(status, ""))
        message = f"第 {view.number(cid)} 处，{view.DECISION_ZH[status]}。"
        if nxt:
            message += f" 已定位到第 {view.number(nxt)} 处。"
        review_feedback.set_text(message)

    def undo(direction: str = "undo", *, from_toolbar: bool = False) -> None:
        note_sessions.clear()
        entry = sess.travel(direction)
        if entry is None:
            return
        target = next(iter(entry["decisions"]), None) if len(entry["decisions"]) == 1 else entry["cur"]
        if target and target not in sess.visible():
            sess.flt = "all"
            sess.search, sess.notes_only = "", False
            search_field.value = ""
            notes_filter.value = False
        current = target or sess.cur
        if current is not None:
            select(current, scroll=True, changed=tuple(entry["decisions"]),
                   focus="" if from_toolbar else ".wb-decision-title")
        else:
            after_change(list(entry["decisions"]))
        if from_toolbar:
            opposite = "redo" if direction == "undo" else "undo"
            target_action = direction if (sess.undo if direction == "undo" else sess.redo) else opposite
            focus_in(shell, f".wb-history [data-action={target_action}]:enabled")
        review_feedback.set_text(f"{'已撤销' if direction == 'undo' else '已重做'} {entry['label']}。")

    def save_note(cid: str, value: str) -> None:
        value = (value or "").strip()
        if value == sess.by_id[cid]["decision"]["note"]:
            return
        if cid not in note_sessions:
            sess.record([cid], f"第 {view.number(cid)} 处 修改说明")
            note_sessions.add(cid)
            history_controls.refresh()  # 同一轮文字编辑不会继续改变审阅历史按钮。
        state.set_note(review, cid, value)
        saving.changed()
        if sess.search.strip() or sess.notes_only:
            update_navigation(refilter=True)

    def finish_note(cid: str) -> None:
        note_sessions.discard(cid)
        saving.save()

    def set_filter(name: str, *, scroll: bool = True) -> None:
        sess.flt = name
        vis = sess.visible()
        if sess.cur not in vis and vis:
            select(vis[0], scroll=scroll and not navigation_open, close_navigation=False, refilter=True)
        else:
            update_navigation(refilter=True)
            saving.save()

    def pick_filter(name: str) -> None:
        set_filter(name)
        focus_in(navigation, '.wb-filter [aria-pressed="true"]')

    def pick_row(cid: str) -> None:
        was_open = navigation_open
        select(cid, scroll=True)
        if not was_open:
            focus_in(navigation, '.wb-row[aria-current="true"]')

    def search_changes(value: str) -> None:
        sess.search = value or ""
        set_filter(sess.flt, scroll=False)

    def filter_notes(value: bool) -> None:
        sess.notes_only = value
        set_filter(sess.flt)

    def step(delta: int, *, focus: str = ".wb-decision-title") -> None:
        select(state.step(sess.order, sess.visible(), sess.cur, delta), scroll=True, focus=focus)

    def batch_dialog(status: str) -> None:
        targets = state.batch_targets(review, sess.visible())
        label = view.DECISION_ZH[status]
        with ui.dialog() as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", f"批量{label}")
            tag("p", f"“{filter_labels[sess.flt]}”里待定的 {len(targets)} 处：{state.describe_ids(targets)}。已决定的不受影响，可整体撤销。"
                if targets else f"“{filter_labels[sess.flt]}”里没有待定的修改。")
            def confirm() -> None:
                done = sess.batch(targets, status)
                after_change(done)
                dialog.close()
            with ui.element("div").classes("wb-actions"):
                confirm_button = button(label, confirm, "wb-btn primary")
                if not targets:
                    confirm_button.props("disabled")
                button("取消", dialog.close)
        dialog_focus(dialog, shell, ".wb-menu")
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    def restore_dialog() -> None:
        def show_error(message: str) -> None:
            restore_error.set_content(html.escape(message))

        async def upload(event) -> None:
            nonlocal review
            try:
                message = sess.restore(await event.file.read())
            except (state.ReviewError, ReviewDataError) as exc:
                show_error(str(exc))
                return
            review = sess.review
            note_sessions.clear()
            search_field.value = ""
            notes_filter.value = False
            saving.save()
            desk.refresh()
            refresh_review_controls()
            dialog.close()
            ui.notify(message)

        with ui.dialog() as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", "恢复进度")
            tag("p", "选择用当前两版文稿导出的 .json 文件。成功后会替换当前的审阅决定和修改意见，并清空撤销记录。")
            file_picker("选择进度文件（.json）", ".json", upload, show_error,
                        rejected="请选择验收台导出的 .json 进度文件。")
            restore_error = tag("div", "", "wb-err").props("role=alert")
            button("取消", dialog.close)
        dialog_focus(dialog, shell, ".wb-menu")
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    def set_save(enabled: bool) -> None:
        sess.save_enabled = enabled
        saving.save()

    # ------------------------------ 工具栏

    @ui.refreshable
    def progress() -> None:
        p = state.progress(review)
        share = (p["decided"] / p["total"] * 100) if p["total"] else 100
        tag("div", tip(f'<span class="wb-num">已处理 <b>{p["decided"]}</b> / {p["total"]}</span>'
                       f'<span class="wb-track"><i style="width:{share:.0f}%"></i></span>',
                       f'本篇共有 {p["total"]} 处改动，还有 {p["total"] - p["decided"]} 处待处理。'), "wb-prog")

    async def change_document() -> None:
        await save_progress()
        leave()

    def history_dialog(*, from_menu: bool = False) -> None:
        async def open_round(index: int) -> None:
            if switching[0]:
                return
            switching[0] = True
            try:
                await save_progress()
                await switch_round(index)
                if not dialog.is_deleted:
                    dialog.close()
            finally:
                switching[0] = False

        switching = [False]
        with ui.dialog().props('aria-label="审阅轮次"') as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", "审阅轮次")
            with ui.element("div").classes("wb-round-list"):
                for index in range(len(sess.rounds), -1, -1):
                    label = f"第 {index + 1} 轮"
                    if index == round_index:
                        ui.label(f"{label} · 正在查看").classes("wb-msg wb-round-current")
                    else:
                        action = "继续审阅" if index == len(sess.rounds) else "查看"
                        button(f"{action}第 {index + 1} 轮", lambda index=index: open_round(index), "wb-btn wb-round-row")
            with ui.element("div").classes("wb-actions wb-end"):
                button("关闭", dialog.close)
        dialog_focus(dialog, shell, ".wb-menu" if from_menu else ".wb-round-button")
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    def sent_dialog() -> None:
        sent = sess.rounds[round_index].get("sent") or {}
        with ui.dialog().props('aria-label="已发送的修改意见"') as dialog, ui.element("div").classes(
                "wb-dialog wb-preview wb-model-preview"):
            tag("h3", "已发送的修改意见")
            ui.label(sent.get("destination", "")).classes("wb-msg")
            params = sent.get("options")
            if isinstance(params, dict):
                scope = SCOPE_LABELS.get(params.get("scope"), "")
                layout = LAYOUT_OPTIONS.get(params.get("layout"), "")
                ui.label(f"第 {round_index + 1} 轮 → 第 {round_index + 2} 轮 · {scope} · {layout}").classes("wb-msg")
                if instructions := params.get("instructions"):
                    with disclosure("当时发送的修订指令", "wb-send-details"):
                        article(instructions)
                if references := params.get("references"):
                    with disclosure("当时发送的学术修订参考", "wb-send-details"):
                        for label, guidance in references.items():
                            tag("h5", label)
                            article(guidance)
                for message in params.get("conversation", []):
                    if message.get("role") == "user":
                        with disclosure("当时补充的要求", "wb-send-details"):
                            article(message["content"])
                for execution in params.get("executions", []):
                    ui.label(f"{execution['id']} · {execution['detail']}").classes("wb-msg")
                if overrides := params.get("decision_overrides"):
                    labels = "、".join(f"第 {view.number(cid)} 处" for cid in overrides)
                    ui.label(f"你已确认覆盖{labels}的原有裁决。").classes("wb-msg")
                if params.get("pending"):
                    ui.label(f"待定处以{params.get('baseline', '')}对照，发送时{params.get('pending_inclusion', '')}待定意见。").classes("wb-msg")
                if isinstance(sent.get("created_at"), str):
                    ui.label(sent["created_at"].replace("T", " ", 1)).classes("wb-msg")
            article(sent.get("feedback", ""))
            with ui.element("div").classes("wb-actions wb-end"):
                button("关闭", dialog.close)
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    # ------------------------------ 左：修改目录

    @ui.refreshable
    def nav_filters() -> None:
        vis = sess.visible()
        labels = {key: f'{label}<span class="wb-num">{len(vis if key == sess.flt else sess.visible(key))}</span>'
                  for key, label in filter_labels.items()}
        segmented(labels, sess.flt, pick_filter, "wb-seg wb-filter")
        if sess.reference_suggestions:
            tag("div", f"已按两版出处差异筛出 {len(sess.reference_suggestions)} 处待核对。", "wb-filter-status").props("role=status")
        elif radar_saturated:
            tag("div", "逐句模型将本轮改动全部标为待核对。", "wb-filter-status").props("role=status")
        if vis and sess.flt not in ("all", "pending"):
            changes = sess.by_id
            pending = sum(changes[cid]["decision"]["status"] == "pending" for cid in vis)
            tag("div", f"这组还有 {pending} 处待处理" if pending else "这组已处理完", "wb-filter-status").props("role=status")

    def repaint_nav_row(row: ui.html, change: dict) -> None:
        cid = change["id"]
        status = change["decision"]["status"]
        current = cid == sess.cur
        classes = "wb-row" + (" on" if current else "") + (" done" if status != "pending" else "")
        mark = "" if status == "pending" else f'<span class="s">{view.DECISION_ZH.get(status, status)}</span>'
        summary = html.escape(render.short_summary(change))
        row.set_content(f'<span class="n wb-num">{view.number(cid)}</span><span class="t">{summary}</span>{mark}')
        row.classes(replace=classes).props(f'aria-current={str(current).lower()}')

    @ui.refreshable
    def nav_list() -> None:
        vis = sess.visible()
        nav_rows.clear()
        if sess.display == "drafts":
            tag("div", f"{len(vis)} 条逐句记录", "wb-filter-status")
            button("查看逐句记录", lambda: switch_display("compare"))
            return
        with ui.element("div").classes("wb-list"):
            if not vis:
                tag("div", "没有匹配的修改" if sess.search or sess.notes_only else
                    "没有待定的修改" if sess.flt == "pending" else "没有修改", "wb-empty")
            changes = sess.by_id
            for cid in vis:
                row = tag("button").on("click", lambda cid=cid: pick_row(cid))
                nav_rows[cid] = row
                repaint_nav_row(row, changes[cid])
            if hint_list:
                items = "".join(
                    f'<div class="wb-hint"><span class="t">{view.HINT_CATEGORIES[h["category"]]}“{html.escape(h["text"])}”</span>'
                    f'<span class="s">{("第 " + view.number(h["change_id"]) + " 处") if h["change_id"] else ("第 " + str(h["sentence"]["paragraph"] + 1) + " 段")}</span></div>'
                    for h in hint_list)
                tag("details", f'<summary>{tip("原稿已有", "原稿里本来就有、这一轮没有改动的套话和防御表述。只作提示，不计入进度。")}'
                               f'<span class="wb-num">{len(hint_list)}</span></summary>{items}', "wb-disclosure wb-hints", trusted=False)

    def update_navigation(ids: Iterable[str | None] | None = (), *, refilter: bool = False) -> None:
        if refilter:
            vis = sess.visible()
            nav_filters.refresh()
            if ids is None or not vis or list(nav_rows) != vis:
                nav_list.refresh()
                return
        changes = sess.by_id
        for cid in dict.fromkeys(ids or ()):
            if cid in nav_rows:
                repaint_nav_row(nav_rows[cid], changes[cid])

    # ------------------------------ 右：当前修改

    @ui.refreshable
    def side() -> None:
        nonlocal note_editor
        note_editor = None
        if sess.display == "drafts":
            tag("p", "先对照两版全文，再查看逐句记录并作出决定。", "wb-empty")
            return
        c = sess.by_id.get(sess.cur) if sess.cur else None
        if c is None:
            tag("div", "选择一处修改", "wb-empty")
            return
        cid, dec = c["id"], c["decision"]
        status = dec["status"]
        with ui.element("div"):
            with ui.element("div").classes("wb-head"):
                tag("h2", f'第 {view.number(cid)} 处<span class="wb-state {status}">{view.DECISION_ZH.get(status, status)}</span>', "wb-decision-title").props("tabindex=-1")
                with ui.element("div").classes("wb-pager"):
                    tag("button", "‹", "wb-icon").props("aria-label=上一处 title=上一处").on("click", lambda: step(-1, focus='[aria-label=上一处]'))
                    tag("button", "›", "wb-icon").props("aria-label=下一处 title=下一处").on("click", lambda: step(+1, focus='[aria-label=下一处]'))
            tag("span", "", "wb-mobile-save").bind_content_from(saving, "message")
            natures = view.nature(c)
            k = render.edit_count(c)
            if natures or k > 1:
                with ui.element("div").classes("wb-tags"):
                    for label, tone in natures:
                        tag("span", label, "wb-tag" + (" warn" if tone == "warn" else ""))
                    if k > 1:
                        tag("span", tip(f"{k} 处字词变化", "这一处里有多处字词变化，只能整处采用或保留；要逐词取舍请用“自行编辑”。"), "wb-tag")
        ev = render.evidence(c)
        effect = view.likely_purpose(c)
        tag("div", render.card_pair_html(c), "wb-pair wb-serif", trusted=False)
        rows = []
        if cid in sess.reference_suggestions:
            evidence = "；".join(f"{'原稿' if side == 'original' else '改稿'} {text}"
                                 for side, text in sess.reference_suggestions[cid])
            rows.append(("出处待核对", html.escape(evidence)))
        if not radar_saturated and sess.model_suggestions is not None and cid in sess.model_suggestions:
            rows.append((MODEL_NAME, "建议优先审阅这处修改"))
        if ev["rules"]:
            rows.append((tip("检查依据", "词表规则的命中，不是语义判断。"), html.escape("；".join(ev["rules"]))))
        if ev["model"]:
            rows.append((tip("模型提示", "语义模型的判断，可能有误。"), html.escape("；".join(ev["model"]))))
        if rows:
            tag("div", "".join(f'<span class="k">{key}</span><span>{value}</span>' for key, value in rows), "wb-dl", trusted=False)
        with disclosure("改动细节", "wb-change-details"):
            tag("h3", "文字变化")
            with ui.element("ul"):
                for item in ev["observed"]:
                    tag("li", html.escape(item), trusted=False)
            if effect:
                tag("h3", "可能产生的影响")
                tag("p", html.escape(effect[0]), trusted=False)
        if historical:
            if dec["note"]:
                tag("h3", "修改意见", "wb-msg")
                tag("p", html.escape(dec["note"]), "wb-archived-note")
            if cid in sess.drafts:
                tag("h3", "未提交的编辑草稿", "wb-msg")
                tag("p", html.escape(sess.drafts[cid]), "wb-archived-note")
            return
        with ui.element("div").classes("wb-decide"):
            for choice, label, control in (("accepted", "采用改稿", "wb-accept"), ("rejected", "保留原稿", "wb-reject")):
                pressed = status == choice
                hotkey = "A" if choice == "accepted" else "R"
                button(label, lambda choice=choice: decide(cid, choice),
                       f"wb-btn tall {control}" + (" on" if pressed else "")).props(
                    f'aria-pressed={str(pressed).lower()} title="{label}（Alt+Shift+{hotkey}）" aria-keyshortcuts="Alt+Shift+{hotkey}"')
        with ui.element("div").classes("wb-minor"):
            tag("button", "自行编辑", "wb-link wb-edit-trigger").on("click", lambda: edit_dialog(c))
            tag("button", "稍后处理", "wb-link wb-defer").on("click", lambda: step(+1, focus=".wb-defer"))
            if status != "pending":
                tag("button", "改回待定", "wb-link").on("click", lambda: decide(cid, "pending"))
        with ui.element("div").classes("wb-fb"):
            with ui.element("div").classes("wb-help-line"):
                tag("span", "修改意见", "wb-msg")
                help_icon("修改意见", "像聊天一样写清要改什么、怎么改。生成新稿时全部自动发送，优先于之前的采用、保留原稿和自行编辑。")
            tag("p", "直接告诉模型怎么改。建议自动保存，发送时优先于之前的取舍。", "wb-msg")
            area = ProseEditor(dec["note"], label="修改意见", on_commit=lambda value: save_note(cid, value)).classes("wb-note-editor")
            note_editor = area

            async def note_blurred() -> None:
                await area.settled_value()
                finish_note(cid)

            area.on("focusout", note_blurred, [])
            button("交给模型修改", lambda: open_generation(), "wb-btn primary wb-note-send")
            tag("p", "将汇总本文全部修改建议，核对模型后发送。", "wb-msg")
        if cid in sess.drafts:
            button("继续编辑草稿", lambda: edit_dialog(c, from_draft=True), "wb-link wb-draft-trigger")

    def edit_dialog(c: dict, *, from_draft: bool = False) -> None:
        cid, dec = c["id"], c["decision"]
        committed = dec["final_text"] if dec["status"] == "edited" else (c["revised"]["text"] or c["original"]["text"])
        start = sess.drafts.get(cid, committed)
        discarded = False

        def draft_changed(value: str) -> None:
            sess.drafts[cid] = value or ""
            saving.changed()

        with EditorDialog() as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", f"自行编辑第 {view.number(cid)} 处")
            tag("div", "保存后用于终稿；关闭后保留草稿。留空保存会删去这一句。", "wb-msg")
            area = ProseEditor(start, label="自行编辑", on_commit=draft_changed).classes("wb-draft-editor")

            async def save() -> None:
                if area.gate.composing:
                    return
                decide(cid, "edited", await area.settled_value())
                dialog.close()

            def discard() -> None:
                nonlocal discarded
                discarded = True
                sess.drafts.pop(cid, None)
                saving.save()
                dialog.close()
                side.refresh()

            shortcuts(dialog, save=save)
            focused = False

            def focus_editor(event) -> None:
                nonlocal focused
                if not focused and event.args["width"] > 0 and event.args["height"] > 0:
                    focused = True
                    dialog.run_method("focus", ".cm-content")

            area.on("geometry-change", focus_editor)

            async def closed() -> None:
                if not discarded:
                    await area.settled_value()
                saving.save()
                side.refresh()
                trigger = ".wb-draft-trigger" if from_draft and cid in sess.drafts else ".wb-edit-trigger"
                focus_in(decision_anchor, trigger, scroll=True)

            dialog.on("hide", closed)
            with ui.element("div").classes("wb-actions"):
                button("保存", save, "wb-btn primary").props("title=保存（⌘/Ctrl+S）")
                button("关闭", dialog.close)
                button("放弃草稿", discard, "wb-link")
        dialog.open()

    # ------------------------------ 导出

    async def export_dialog() -> None:
        await save_progress()
        dialog = show_export(sess, review, generate=open_generation if models is not None else None)
        dialog_focus(dialog, shell, ".wb-export")

    generation_dialog = None
    opening_generation = False

    async def open_generation(choice: dict | None = None, *, close_export=None) -> None:
        nonlocal generation_dialog, opening_generation
        if opening_generation or (generation_dialog is not None and not generation_dialog.is_deleted):
            return
        opening_generation = True
        try:
            await save_progress()
            if shell.is_deleted:
                return
            options = dict(DEFAULT_REVISION_OPTIONS) if choice is None else choice

            async def prepare(selected: dict) -> RevisionRequest:
                snapshot = copy(sess)
                snapshot.review = state.new_review(sess.review)
                snapshot.formats = dict(sess.formats)
                snapshot.drafts = dict(sess.drafts)
                return await run.io_bound(RevisionRequest.prepare, snapshot, dict(selected))

            def matches(request: RevisionRequest) -> bool:
                return not shell.is_deleted and request.matches(sess)

            request = await prepare(options)
            if shell.is_deleted:
                return
            if not matches(request):
                ui.notify("审阅内容已变化。请重新打开「生成新稿」。", type="warning")
                return
            with shell:
                generation_dialog = models.revision(
                    request, options=options, prepare=prepare,
                    matches=matches,
                    advance=create_round,
                    filename=f"{sess.title}_第{round_index + 2}轮候选稿.{sess.formats.get('revised', 'txt')}",
                )
            dialog_focus(generation_dialog, shell, ".wb-export" if close_export else ".wb-generate")
            if close_export is not None:
                close_export()
        except (PendingDecisions, MergeError, ReviewDataError) as exc:
            ui.notify(str(exc), type="negative")
        finally:
            opening_generation = False

    async def create_round(request: RevisionRequest, candidate: str, destination: str,
                           decision_overrides: tuple[str, ...]) -> None:
        await save_progress()
        report = await run.io_bound(_audit, request.baseline, candidate)
        if shell.is_deleted or not request.matches(sess):
            raise ValueError("审阅内容已变化。请按当前决定重新生成，或下载这份候选稿。")
        sess.advance(request.baseline, candidate, report, feedback=request.feedback, destination=destination,
                     revision_options={"scope": request.scope, "layout": request.layout, "pending": request.pending,
                                       "pending_inclusion": request.pending_inclusion, "baseline": request.pending_version,
                                       "instructions": request.instructions,
                                       "references": request.reference_guidance,
                                       "conversation": [{"role": role, "content": text} for role, text in request.conversation],
                                       "executions": [entry.payload for entry in request.execution_log],
                                       "decision_overrides": list(decision_overrides)})
        rerender(f"已开启第 {len(sess.rounds) + 1} 轮验收。")

    desk = ui.refreshable(document.render)

    # ------------------------------ 布局

    def reveal(cid: str) -> None:
        if sess.display == "drafts":
            switch_display("compare")
        refilter = cid not in sess.visible()
        if refilter:
            sess.flt, sess.search, sess.notes_only = "all", "", False
            search_field.value = ""
            notes_filter.value = False
        select(cid, scroll=True, refilter=refilter, focus=".wb-decision-title")

    def quick_jump() -> None:
        ids = {change["id"]: f'第 {view.number(change["id"])} 处 · {render.short_summary(change)}'
               for change in review["changes"]}
        if not ids:
            return

        selection = None

        def picked(cid: str) -> None:
            nonlocal selection
            selection = cid
            dialog.close()

        def closed() -> None:
            dialog.delete()
            if selection is not None:
                reveal(selection)
            else:
                focus_in(shell, ".wb-menu")

        with ui.dialog().props('aria-label="定位修改"') as dialog, ui.element("section").classes("wb-dialog") as body:
            tag("h3", "定位修改")
            picker = SearchPicker(ids, sess.cur or next(iter(ids)), "修改编号或摘要", picked)
            button("取消", dialog.close)
        dialog.on("show", lambda: focus_in(body, ".wb-picker input"), [])
        dialog.on("hide", closed, [])
        picker.props("open")
        dialog.open()

    def preview_dialog() -> None:
        saving.save()
        dialog = show_preview(sess, review)
        dialog_focus(dialog, shell, ".wb-preview-trigger")

    @ui.refreshable
    def history_controls() -> None:
        with ui.element("div").classes("wb-history").props('role=group aria-label="编辑历史"'):
            for direction, label, stack, shortcut in (
                ("undo", "撤销", sess.undo, "Meta+Z Control+Z"),
                ("redo", "重做", sess.redo, "Meta+Shift+Z Control+Y Control+Shift+Z"),
            ):
                detail = f' {stack[-1]["label"]}' if stack else "（暂无可用操作）"
                title = html.escape(f"{label}{detail} · {keys[direction]}", quote=True)
                control = tag("button", HISTORY_ICONS[direction], "wb-icon wb-undo").props(
                    f'aria-label="{label}" title="{title}" aria-keyshortcuts="{shortcut}" data-action={direction}').on(
                        "click", lambda direction=direction: undo(direction, from_toolbar=True))
                if not stack:
                    control.props("disabled")

    @ui.refreshable
    def display_switch() -> None:
        with ui.element("div").classes("wb-view-controls"):
            displays = {"proof": "连续正文", "compare": "并排对校"}
            if sess.unpaired_rewrite:
                displays = {"drafts": "全文对照", **displays}
            segmented(displays, sess.display, switch_display)
            button("预览", preview_dialog, "wb-btn wb-toolbar-action wb-preview-trigger")
            help_icon("修订标记", "下划线表示新增，删除线表示删去；两者相邻表示替换。排版变化单独提示。")
            if not historical:
                history_controls()

    def switch_display(value: str) -> None:
        changes_review_controls = "drafts" in (sess.display, value)
        sess.display = value
        desk.refresh()
        if changes_review_controls:
            update_navigation(None, refilter=True)
            side.refresh()
        pending_map.refresh()
        saving.save()
        display_switch.refresh()

    def radar_summary() -> str:
        total = len(sess.order)
        hits = len(sess.model_suggestions or ())
        detail = f"本轮 {total} 条记录，{hits} 条超过冻结阈值。"
        if sess.reference_suggestions:
            detail += f" 逐句模型全部命中，已用两版出处差异筛出 {len(sess.reference_suggestions)} 处。"
        elif radar_saturated:
            detail += " 逐句模型全部命中，当前没有出处差异可用于缩小范围。"
        return f"项目自训的 run03 分类器，在本机导入两版文稿时自动运行。{detail} 生成模型在「模型连接」中单独选择。"

    def radar_details() -> None:
        with ui.dialog().props('aria-label="审阅雷达"') as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", "审阅雷达")
            ui.label(radar_summary()).classes("wb-msg")
            button("关闭", dialog.close)
        dialog_focus(dialog, shell, ".wb-radar")
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    def rename_document() -> None:
        def confirm() -> None:
            title = title_field.value.strip()
            if not title:
                title_field.error = "请输入文稿名称"
                return
            if any(character in '/\\' or ord(character) < 32 for character in title):
                title_field.error = "名称不能含斜杠或控制字符"
                return
            sess.title = title
            title_button.set_content(html.escape(title))
            saving.save()
            dialog.close()

        with ui.dialog().props('aria-label="重命名文稿"') as dialog, ui.element("div").classes("wb-dialog"):
            tag("h3", "重命名文稿")
            title_field = ui.input("文稿名称", value=sess.title).props("outlined maxlength=120 data-autofocus")
            title_field.on("keydown.enter", confirm, [])
            with ui.element("div").classes("wb-actions wb-end"):
                button("取消", dialog.close)
                button("保存", confirm, "wb-btn primary")
        dialog_focus(dialog, shell, ".wb-doc-rename")
        dialog.on("hide", dialog.delete, [])
        dialog.open()

    def toggle_auto(value: bool) -> None:
        sess.auto_advance = value

    versions = html.escape(f'{sess.names.get("original") or "原稿"} → {sess.names.get("revised") or "改稿"}')
    with ui.element("div").classes("wb-shell").props("tabindex=-1 autofocus") as shell:
        for modifier in ("ctrl", "meta"):
            shell.on(f"keydown.{modifier}.k.exact.prevent.stop", quick_jump, [])
        if not historical:
            def decide_by_key(event) -> None:
                if sess.display != "drafts" and sess.cur is not None:
                    decide(sess.cur, "accepted" if event.args == "KeyA" else "rejected")
            shell.on("keydown", decide_by_key, js_handler="""(event) => {
                if (event.defaultPrevented || event.repeat || event.isComposing ||
                    !event.altKey || !event.shiftKey || event.metaKey || event.ctrlKey ||
                    !['KeyA', 'KeyR'].includes(event.code) ||
                    event.target.closest('input, textarea, select, [contenteditable=true], [role=dialog], [role=menu]')) return;
                event.preventDefault();
                emit(event.code);
            }""")
            shortcuts(shell, save=save_progress, undo=undo, redo=lambda: undo("redo"),
                      previous=lambda: step(-1), next_change=lambda: step(1))
        with ui.element("header").classes("wb-bar wb-bar-generation" if models is not None else "wb-bar"):
            with ui.element("div").classes("wb-bar-side") as document_anchor:
                navigation_button = ui.button(icon="format_list_bulleted", color=None,
                                              on_click=lambda: toggle_navigation(True)).props(
                    'flat dense round aria-label="修改目录" aria-expanded=false').classes("wb-nav-toggle")
                ui.icon("description").classes("wb-doc-icon").props("aria-hidden=true")
                with ui.element("div").classes("wb-document"):
                    if historical:
                        tag("div", tip(html.escape(sess.title), versions), "wb-doc", trusted=False)
                    else:
                        title_button = button(sess.title, rename_document, "wb-doc wb-doc-rename").props(
                            'type=button aria-haspopup=dialog')
                        title_button.tooltip("点击重命名文稿")
                    with ui.element("div").classes("wb-document-meta"):
                        if sess.rounds and switch_round is not None:
                            button(f"第 {round_index + 1} 轮", history_dialog, "wb-link wb-round-button").props(
                                'aria-haspopup=dialog aria-label="切换审阅轮次"')
                            if historical:
                                tag("span", "历史", "wb-mode")
                        else:
                            tag("span", "验收台", "wb-brand")
                        if sess.model_suggestions is not None:
                            tag("span", MODEL_NAME, "wb-mode")
                            button("?", radar_details, "wb-help wb-radar").props(
                                'type=button aria-label="审阅雷达说明" aria-haspopup=dialog')
                        elif sess.report["run"]["mode"] == "rules_only":
                            tag("span", tip("规则模式", "语义模型没有加载，本次只用词表规则检查。"), "wb-mode")
            display_switch()
            with ui.element("div").classes("wb-bar-side end") as export_actions:
                if not historical:
                    button("", save_progress, "wb-save-status").bind_content_from(saving, "message").props(
                        'aria-label="保存进度" title="保存进度（⌘/Ctrl+S）"')
                progress()
                export_class = "wb-btn wb-toolbar-action" if models is not None else "wb-btn wb-toolbar-action primary"
                export_button = button("导出", export_dialog, export_class + " wb-export").props("aria-haspopup=dialog")
                if models is not None:
                    generate_button = button("生成新稿", open_generation,
                                             "wb-btn wb-toolbar-action primary wb-generate").props(
                        'aria-haspopup=dialog title="连接模型并按审阅意见生成新稿"')
                if dark is not None:
                    appearance_menu(dark)
                with ui.button(icon="more_horiz", color=None).props("flat dense aria-label=更多").classes("wb-menu") as more_button:
                    with ui.menu().classes("wb-more-menu").props('role=menu aria-label="更多操作" transition-show=fade transition-hide=fade') as more_menu:
                        if historical:
                            menu_items = [menu_action("查看已发送的修改意见…", sent_dialog),
                                          menu_action("审阅轮次…", lambda: history_dialog(from_menu=True)),
                                          menu_action("返回当前轮…", lambda: switch_round(len(sess.rounds)))]
                        else:
                            menu_items = [menu_action("保存进度", save_progress, keys["save"])]
                            menu_items.append(menu_action("定位修改…", quick_jump, "⌘/Ctrl+K"))
                            if models is not None:
                                menu_items.append(menu_action("模型连接…", lambda: models.settings(
                                    focus_back=(shell, ".wb-menu"),
                                    local_review=radar_summary() if sess.model_suggestions is not None else "")))
                            if sess.rounds:
                                menu_items.append(menu_action("审阅轮次…", lambda: history_dialog(from_menu=True)))
                            ui.separator()
                            menu_items.append(menu_action("批量采用改稿…", lambda: batch_dialog("accepted")))
                            menu_items.append(menu_action("批量保留原稿…", lambda: batch_dialog("rejected")))
                            ui.separator()
                            menu_items.append(menu_action("恢复进度…", restore_dialog))
                            menu_items.append(menu_action("更换文稿…", change_document))
                            ui.separator()
                            menu_items.append(menu_toggle("决定后自动前进", sess.auto_advance, toggle_auto))
                            menu_items.append(menu_toggle("在本机保存进度", sess.save_enabled, set_save))
                            if guide is not None:
                                ui.separator()
                                menu_items.append(menu_action("新手教程…", guide.open))
                        menu_navigation(more_menu, menu_items)
        review_feedback = ui.label("").classes("wb-sr-only").props("role=status aria-live=polite")
        with ui.element("main").classes("wb-main") as main_panel:
            button("", lambda: toggle_navigation(False), "wb-nav-backdrop").props('tabindex=-1 aria-hidden=true')
            with ui.element("nav").classes("wb-nav") as navigation:
                navigation.props('aria-label="修改目录"')
                navigation_button.props(f"aria-controls=ng-{navigation.id}")
                navigation.on("keydown.esc.prevent.stop", lambda: toggle_navigation(False))
                navigation.on("keydown.tab", js_handler="""(event) => {
                    const nav = event.currentTarget;
                    if (!nav.closest('.wb-nav-open') || !matchMedia('(max-width: 900px)').matches) return;
                    const controls = [...nav.querySelectorAll('button, input, summary, [tabindex="0"]')]
                        .filter(el => el.tabIndex >= 0 && !el.disabled && el.getClientRects().length);
                    const bound = event.shiftKey ? controls[0] : controls.at(-1);
                    if (event.target === bound) {
                        event.preventDefault();
                        (event.shiftKey ? controls.at(-1) : controls[0])?.focus();
                    }
                }""")
                with ui.element("div").classes("wb-nav-head"):
                    with ui.element("div").classes("wb-help-line"):
                        tag("span", "修改目录")
                        filters_help = "各数字是当前匹配的处数。「待处理」只看未决定的修改。「检查提示」按检查发现筛选，「无提示」显示其余修改。"
                        if sess.reference_suggestions:
                            filters_help += "「出处待核对」显示在另一版全文中找不到相同标记的出处。"
                        elif MODEL_FILTER in filter_labels:
                            filters_help += "「模型优先」按审阅雷达的推荐筛选，可与「检查提示」重叠。"
                        help_icon("目录筛选", filters_help)
                    ui.button(icon="close", color=None, on_click=lambda: toggle_navigation(False)).props(
                        'flat dense round aria-label="关闭目录"')
                search_field = ui.input(value=sess.search, placeholder="查找修改与意见",
                                        on_change=lambda e: search_changes(e.value)).props("outlined dense clearable debounce=250").classes("wb-search")
                with search_field.add_slot("prepend"):
                    ui.icon("search").props("aria-hidden=true")
                text_undo(search_field)
                notes_filter = ui.checkbox("有修改意见", value=sess.notes_only,
                                           on_change=lambda e: filter_notes(e.value)).props("dense").classes("wb-notes-filter").tooltip(
                                               "仅显示填写了文字意见的修改")
                if sess.model_error:
                    tag("div", html.escape(f"模型优先筛选暂不可用。{sess.model_error}"), "wb-msg")
                nav_filters()
                nav_list()
            with ui.element("div").classes("wb-reader"):
                desk()
                pending_map()
            with ui.element("aside").classes("wb-side") as decision_anchor:
                side()
        opened_guide = False
        if guide is not None:
            steps = []
            if review["changes"]:
                steps.extend([
                    GuideStep(document_anchor, "定位一处修改", "选择正文里的改动标记，或从修改目录筛选。窄屏可从左上角打开目录。"),
                    GuideStep(decision_anchor, "逐处决定", "采用或保留后可继续下一处。用 Option／Alt＋方向键定位，按 ⌘／Ctrl＋Z 撤销决定并回到该处。", placement="inside", align="right"),
                ])
            steps.append(GuideStep(export_button, "预览与导出", "用「预览」查看合并结果。用「导出」下载文稿、修改意见或进度文件。",
                                   align="right", anchor=export_actions))
            if models is not None:
                steps.append(GuideStep(
                    generate_button, "连接模型并生成新稿",
                    "在这里选择本机 CLI 或 API。发送前，核对确认按钮上方的模型和发送范围。",
                    align="right", anchor=export_actions))
            steps.append(GuideStep(more_button, "更多操作", "从这里批量处理、恢复进度，或调整是否在决定后自动前进。", align="right"))
            opened_guide = guide.mount(steps, shell, can_generate=models is not None)
        if not opened_guide:
            def locate_initial_change() -> None:
                shell.client.run_javascript(f"""
                    if (document.activeElement === document.body) getHtmlElement({shell.id})?.focus({{preventScroll: true}});
                """)
                document.scroll_to(sess.cur)
            ui.timer(0.1, locate_initial_change, once=True)
    if not historical:
        saving.save()
    if message:
        ui.notify(message, timeout=6000)


# ---------------------------------------------------------------- 页面与启动

@ui.page("/")
async def index() -> None:
    ui.add_css(STYLE)
    ui.add_head_html(f'<script type="module" src="{PROSE_URL}/prose_history.js"></script>')
    ui.colors(primary="#0a60cf")
    ui.page_title("验收台")
    storage = app.storage.user
    storage.setdefault(APPEARANCE_KEY, None)
    dark = ui.dark_mode(None).bind_value(storage, APPEARANCE_KEY)
    sess = Session(storage, beta_model=_beta_model)
    client = ui.context.client
    native = desktop_page_setup is not None
    if native:
        desktop_page_setup()
    request = client.request
    local_address = request.url.hostname or ""
    local_client = request.client.host if request.client else ""
    host = "127.0.0.1" if native else os.environ.get("ACCEPTANCE_DESK_HOST", "127.0.0.1")
    models = ModelDialogs(storage) if loopback(host) and loopback(local_address) and loopback(local_client) else None
    if models is not None:
        client.on_delete(models.dispose)
    guide = Guide(storage)
    root = ui.element("div").classes("w-full")

    def show_workbench(message: str | None = None) -> None:
        root.clear()
        with root:
            workbench_view(sess, show_import, message, dark, guide=guide, models=models, rerender=show_workbench,
                           switch_round=show_round)

    async def show_round(index: int) -> None:
        if index == len(sess.rounds):
            show_workbench()
            return
        saved_round = sess.rounds[index]
        source_view = tuple(root.default_slot.children)
        history = Session(beta_model=_beta_model)
        history.save_enabled = False
        try:
            report = await run.io_bound(_audit, saved_round["original"], saved_round["revised"])
            history.load_snapshot(saved_round, report)
        except (KeyError, TypeError, ValueError, ReviewDataError, state.ReviewError):
            ui.notify("这一轮没能打开，当前进度仍在。", type="negative")
            return
        if tuple(root.default_slot.children) != source_view:
            return
        history.rounds = sess.rounds
        root.clear()
        with root:
            workbench_view(history, show_import, dark=dark, switch_round=show_round, viewed_round=index)

    async def resume_saved() -> bool:
        saved = storage.get(STORE_KEY)
        if not saved:
            return False
        try:
            report = await run.io_bound(_audit, saved["original"], saved["revised"])
            if saved is not storage.get(STORE_KEY):
                ui.notify("本机进度刚有更新，请重新打开。", type="warning")
                return False
            message = sess.restore_project(saved, report)
        except (KeyError, TypeError, ValueError, ReviewDataError, state.ReviewError):
            ui.notify("本机进度没能恢复，存档仍保留。", type="warning", timeout=0)
            return False
        show_workbench(message)
        return True

    def show_import() -> None:
        client.desktop_autosave = None
        sess.review = None
        root.clear()
        saved = storage.get(STORE_KEY)
        with root:
            import_view(sess, show_workbench, dark, guide=guide, models=models,
                        resume=resume_saved if isinstance(saved, dict) else None,
                        saved_title=saved.get("title") if isinstance(saved, dict) else None,
                        saved_at=saved.get("saved_at", "") if isinstance(saved, dict) else "")

    if native:
        show_import()
    elif not await resume_saved():
        show_import()


def _storage_secret(path: Path | None = None) -> str:
    """浏览器会话 cookie 的签名密钥：首次运行时生成，存在用户主目录，不进仓库。"""
    if path is None and os.environ.get("ACCEPTANCE_DESK_SECRET"):
        return os.environ["ACCEPTANCE_DESK_SECRET"]
    path = path or Path.home() / ".acceptance-desk" / "web-secret"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def load_beta_model(model_path: str | Path | None) -> None:
    global _beta_model
    try:
        _beta_model = RuleClassifier.read(Path(model_path).expanduser()) if model_path else None
    except (OSError, ValueError) as exc:
        raise SystemExit(f"无法启动{MODEL_NAME}。{exc}") from exc


def main() -> None:
    load_beta_model(os.environ.get("ACCEPTANCE_DESK_BETA_MODEL"))
    host = os.environ.get("ACCEPTANCE_DESK_HOST", "127.0.0.1")
    port = int(os.environ.get("ACCEPTANCE_DESK_PORT", "8780"))
    ui.run(host=host, port=port, title="验收台", language="zh-CN", reload=False, show=False, dark=None,
           storage_secret=_storage_secret())
