"""工作台的正文与并排对校区域，维护当前页面的修改锚点和页边批注。"""

from __future__ import annotations

import html

from nicegui import ui

from acceptance_desk import layout_differences
from review_ui import render

from . import view
from .controls import tag
from .session import Session


class DocumentPane:
    def __init__(self, session: Session, hints: dict, select):
        self.session = session
        self.hints = hints
        self.select = select
        self.groups: dict[str, list[tuple]] = {}
        self.notes: dict[str, ui.html] = {}

    def _target(self, cid: str, side: str | None = None) -> None:
        element = tag("span", "", "wb-g", trusted=False).props(
            f'tabindex=0 role=button aria-label="查看第 {view.number(cid)} 处修改"')
        for event in ("click", "keydown.enter.prevent", "keydown.space.prevent"):
            element.on(event, lambda: self.select(cid, scroll=False))
        self.groups.setdefault(cid, []).append((element, side))

    def scroll_to(self, cid: str | None) -> None:
        if cid in self.groups:
            anchor = self.groups[cid][0][0]
            anchor.client.run_javascript(f"""
                setTimeout(() => getHtmlElement({anchor.id})?.scrollIntoView({{
                    block: 'nearest', inline: 'nearest'
                }}));
            """)

    def repaint(self, ids) -> None:
        changes = self.session.by_id
        for cid in dict.fromkeys(ids):
            if cid not in self.groups:
                continue
            change = changes[cid]
            current = cid == self.session.cur
            elements = self.groups[cid]
            pair = render.diff_html(change) if elements[0][1] is not None else None
            for element, side in elements:
                content = view.group_html(change, current) if side is None else view.comparison_html(change, side, pair=pair)
                element.set_content(content)
                element.classes(add="cur") if current else element.classes(remove="cur")
            if cid in self.notes:
                note = self.notes[cid]
                note.set_content(f'<span class="n wb-num">{view.number(cid)}</span><span>{html.escape(view.note_text(change))}</span>')
                note.classes(add="on") if current else note.classes(remove="on")

    def render(self) -> None:
        sess = self.session
        self.groups.clear()
        self.notes.clear()
        with ui.element("section").classes("wb-desk"):
            diffs = layout_differences(sess.review, sess.original, sess.revised, markdown="md" in sess.formats.values())
            risks = [diff.markdown_risk for diff in diffs if diff.markdown_risk]
            if diffs:
                tag("div", html.escape(f"另有 {len(diffs)} 处空白差异，导出时选择排版。" + " ".join(risks)), "wb-layout-info", trusted=False)
            if not sess.review["changes"]:
                tag("div", "没有文字修改" if diffs else "两份文稿完全一致", "wb-empty")
            if sess.unpaired_rewrite:
                tag("div", "这两版未能逐句配对。可先查看两版全文，再切换到并排对校查看逐句记录。", "wb-layout-info")
            if sess.display == "drafts":
                self._drafts()
            elif sess.display == "compare":
                self._comparison()
            else:
                self._prose()
            self.repaint(self.groups)

    def _drafts(self) -> None:
        with ui.element("div").classes("wb-compare wb-serif"):
            tag("div", "原稿", "wb-compare-head")
            tag("div", "改稿", "wb-compare-head")
            for label, text in (("原稿", self.session.original), ("改稿", self.session.revised)):
                with ui.element("div").classes("wb-compare-cell"):
                    tag("div", label, "wb-mobile-version")
                    tag("span", html.escape(text), trusted=False)

    def _comparison(self) -> None:
        with ui.element("div").classes("wb-compare wb-serif"):
            tag("div", "原稿", "wb-compare-head")
            tag("div", "改稿", "wb-compare-head")
            for row in view.comparison_rows(self.session.segments, self.session.original, self.session.revised):
                cid = row["change_id"]
                for which in ("original", "revised"):
                    with ui.element("div").classes("wb-compare-cell"):
                        if cid or row[which].strip():
                            tag("div", "原稿" if which == "original" else "改稿", "wb-mobile-version")
                        if row[which + "_gap"]:
                            tag("span", html.escape(row[which + "_gap"]), trusted=False)
                        if cid:
                            self._target(cid, which)
                        else:
                            tag("span", html.escape(row[which]), trusted=False)

    def _prose(self) -> None:
        with ui.element("div").classes("wb-sheet"):
            index = -1
            for para in view.paragraphs(self.session.segments):
                with ui.element("div").classes("wb-txt wb-serif"):
                    para_notes: list[tuple] = []
                    for k, seg in enumerate(para):
                        index += 1
                        gap = seg["gap"].lstrip() if k == 0 else seg["gap"]
                        cid = seg["change_id"]
                        if cid is None:
                            tag("span", view.plain_html(gap + seg["original"], self.hints.get(index, []), len(gap)), trusted=False)
                        else:
                            if gap:
                                tag("span", html.escape(gap).replace("\n", "<br>"), trusted=False)
                            self._target(cid)
                            para_notes.append(("change", cid))
                        para_notes.extend(("hint", h) for h in self.hints.get(index, []))
                with ui.element("div").classes("wb-notes"):
                    for kind, item in para_notes:
                        if kind == "change":
                            self.notes[item] = tag("button", "", "wb-note", trusted=False).on(
                                "click", lambda cid=item: self.select(cid, scroll=True))
                        else:
                            tag("div", f'<span class="n">·</span><span>原稿已有的{view.HINT_CATEGORIES[item["category"]]}</span>', "wb-note hint")
