"""工作台的显示整理：把审计结果整理成段落、批注、提示文字。不依赖界面框架，便于测试。"""

from __future__ import annotations

import html

from acceptance_desk.text import normalize
from review_ui import render

DECISION_ZH = {"pending": "待定", "accepted": "采用改稿", "rejected": "保留原稿", "edited": "自行编辑",
               "auto_passed": "自动通过"}
DECISION_MARK = {"accepted": "采", "rejected": "留", "edited": "编"}
HINT_CATEGORIES = {"slop": "套话", "defensive": "防御表述"}   # 原稿存量提示先只放这两类；正常限定词不算

# 提示性质：由规则命中的代码归并而来，只描述“这处改动可能带来什么”，不是标签，也不是结论
_NATURE = (
    ({"hedge_removed", "certainty_added", "causal_upgrade"}, "论证强度提升", "warn",
     "让结论读起来更肯定"),
    ({"hedge_added", "defensive_added"}, "论证强度减弱", "warn", "让表述更稳妥、留有余地"),
    ({"number_added", "number_removed", "citation_added", "citation_removed"}, "数字或引用变动", "warn", None),
    ({"slop_added"}, "新增套话", "plain", "这类表述通常用来过渡、强调或总结。可以核对改稿是否增加了有用的信息。"),
    ({"sentence_inserted"}, "新增整句", "plain", None),
    ({"sentence_deleted"}, "删除整句", "plain", None),
    ({"connective_added"}, "新增推论连接", "plain", "让前后句读起来有因果或递进"),
)


def number(change_id: str) -> str:
    return str(int(change_id[1:])) if change_id[1:].isdigit() else change_id


def nature(change: dict) -> list[tuple[str, str]]:
    """本处改动的提示性质：[(文字, 语气)]，语气 warn 表示建议细看。没有规则命中时为空。"""
    codes = {r["code"] for r in change["review"]["reasons"]}
    return [(label, tone) for keys, label, tone, _ in _NATURE if codes & keys]


def likely_purpose(change: dict) -> tuple[str, str] | None:
    """说明改写对读者理解的可能影响，并附上对应的检查依据。"""
    if change["alignment"] == "1-0":
        return None
    reasons = change["review"]["reasons"]
    for reason in reasons:
        if reason["code"] == "slop_added":
            marker = next((term for term in reason["terms"] if term in {"由此可见", "不难看出"}), None)
            if marker:
                return (f"「{marker}」把后面的说法写成从前文得出的结论。请核对前文是否支持这个结论。",
                        reason["detail"])
    codes = {r["code"] for r in reasons}
    for keys, _, _, purpose in _NATURE:
        hit = codes & keys
        if hit and purpose:
            basis = "；".join(r["detail"] for r in reasons if r["code"] in hit)
            return purpose, basis
    return None


def paragraphs(segments: list[dict]) -> list[list[dict]]:
    """把 document_segments 的结果按段落分组，段落顺序与原文一致。"""
    out: list[list[dict]] = []
    for seg in segments:
        while len(out) <= seg["paragraph"]:
            out.append([])
        out[seg["paragraph"]].append(seg)
    return out


def group_html(change: dict, current: bool) -> str:
    """正文里的一处修改：待定或当前时显示增删痕迹；已决定且未选中时显示决定后的文字。"""
    dec = change["decision"]
    st = dec["status"]
    mark = number(change["id"]) + (f" {DECISION_MARK[st]}" if st in DECISION_MARK else "")
    if st == "pending" or current:
        body = render.inline_diff_html(change)
    else:
        empty = "没有采用新增的这一句" if change["alignment"] == "0-1" else "这一句已删去"
        body = html.escape(dec["final_text"] or "") or f'<span class="wb-void">（{empty}）</span>'
    return f'<sup class="wb-m">{mark}</sup>' + body.replace("\n", "<br>")


def comparison_html(change: dict, side: str, *, pair: tuple[str, str] | None = None) -> str:
    """对校视图始终显示两版源文本；作者编辑后的结果在右侧决定栏中单列。"""
    if side == "revised" and change["alignment"] == "1-0":
        body = '<span class="ad-empty">（本句已删除）</span>'
    else:
        original, revised = pair if pair is not None else render.diff_html(change)
        body = original if side == "original" else revised
    return f'<sup class="wb-m">{number(change["id"])}</sup>' + body.replace("\n", "<br>")


def comparison_rows(segments: list[dict], original: str, revised: str) -> list[dict]:
    """按完整对齐顺序配对；分别恢复两侧空白，保留重复句、增删句和各自的换段。"""
    texts = {"original": normalize(original), "revised": normalize(revised)}
    cursors = {"original": 0, "revised": 0}
    rows = []
    for seg in segments:
        row = {"change_id": seg["change_id"]}
        for side, full in texts.items():
            text = seg[side]
            cursor = cursors[side]
            if text:
                at = full.find(text, cursor)
                if at < 0 or full[cursor:at].strip():
                    raise ValueError("对校片段与文稿不一致")
                row[side + "_gap"] = full[cursor:at]
                cursors[side] = at + len(text)
            else:
                row[side + "_gap"] = ""
            row[side] = text
        rows.append(row)
    tail = {"change_id": None}
    for side, full in texts.items():
        remaining = full[cursors[side]:]
        if remaining.strip():
            raise ValueError("对校片段遗漏正文")
        tail[side + "_gap"], tail[side] = remaining, ""
    rows.append(tail)
    return rows


def note_text(change: dict, current: bool = False) -> str:
    """页边批注显示改动简述与已作的决定。兼容旧调用的 current 参数，选中样式由正文区域设置。"""
    st = change["decision"]["status"]
    return render.short_summary(change) + ("" if st == "pending" else f" · {DECISION_ZH.get(st, st)}")


def plain_html(text: str, hits: list[dict], shift: int = 0) -> str:
    """未修改的正文片段；其中属于原稿存量提示的词加点线。hits 用句内位置，shift 是句前空白的长度。"""
    parts, pos = [], 0
    marks = sorted((h["local_start"] + shift, h["local_end"] + shift, h) for h in hits if h.get("local_start") is not None)
    for start, end, h in marks:
        if start < pos:
            continue
        parts.append(html.escape(text[pos:start]))
        label = HINT_CATEGORIES[h["category"]]
        parts.append(f'<span class="wb-old" title="原稿已有的{label}，本轮没有改动">{html.escape(text[start:end])}</span>')
        pos = end
    parts.append(html.escape(text[pos:]))
    return "".join(parts).replace("\n", "<br>")


def segment_starts(segments: list[dict], original: str) -> list[int | None]:
    """每个片段在原稿（统一换行后）里的起始位置；原稿侧为空的片段为 None。按顺序查找，重复的句子不会错位。"""
    text = normalize(original)
    starts, cursor = [], 0
    for seg in segments:
        if not seg["original"]:
            starts.append(None)
            continue
        at = text.find(seg["original"], cursor)
        starts.append(at if at >= 0 else None)
        if at >= 0:
            cursor = at + len(seg["original"])
    return starts


def hints_by_segment(segments: list[dict], original: str, hints: list[dict]) -> dict[int, list[dict]]:
    """把核心给出的原稿存量提示（acceptance_desk.original_hints）分到正文片段上。键是片段下标。

    没被修改的句子里的提示带上句内位置（local_start / local_end），正文里可以加点线；
    落在某处修改里、但那个词原样保留的提示挂在该处修改所在的片段上，只在目录和页边列出。
    """
    starts = segment_starts(segments, original)
    by_change = {seg["change_id"]: i for i, seg in enumerate(segments) if seg["change_id"] is not None}
    out: dict[int, list[dict]] = {}
    for h in hints:
        if h["change_id"] is not None:
            i = by_change.get(h["change_id"])
            if i is not None:
                out.setdefault(i, []).append({**h, "local_start": None, "local_end": None})
            continue
        for i, seg in enumerate(segments):
            at = starts[i]
            if seg["change_id"] is None and at is not None and at <= h["start"] and h["end"] <= at + len(seg["original"]):
                out.setdefault(i, []).append({**h, "local_start": h["start"] - at, "local_end": h["end"] - at})
                break
    return out
