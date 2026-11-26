"""按作者决定构建可带回外部模型的修改意见；仅本地生成，不发送。"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from ._derived import checked_source
from .decisions import ReviewDataError
from .evidence import change_evidence, text_opcodes
from .export import _fence
from .text import split_sentences

SCOPE_LABELS = {"local": "局部修订", "rewrite": "整体改写"}
SCOPES = {
    "local": "请按下列意见只修改对应的句段，其余文字尽量保持不变。",
    "rewrite": "请结合全文重新改写结构和表达。作者指定的文本和保留事项应落实到全文，已采用的写法用作参考。",
}
_CHOICES = {"rejected": "保留原稿", "edited": "自行编辑", "accepted": "采用改稿", "pending": "稍后处理"}


def _position(side: dict, sentences) -> dict:
    if not side["text"]:
        return {"start": side["start"], "end": side["end"], "paragraph": None, "sentence": None}
    index = bisect_right([s.start for s in sentences], side["start"]) - 1
    sentence = sentences[index]
    number = sum(s.paragraph == sentence.paragraph for s in sentences[:index + 1])
    return {"start": side["start"], "end": side["end"], "paragraph": sentence.paragraph + 1, "sentence": number}


def _previous(side: dict, sentences) -> dict | None:
    index = bisect_right([s.start for s in sentences], side["start"]) - 1
    if not side["text"] or index <= 0:
        return None
    previous, current = sentences[index - 1], sentences[index]
    if previous.paragraph != current.paragraph:
        return None
    return {"text": previous.text, "start": previous.start, "end": previous.end}


def _excerpts(change: dict) -> list[dict]:
    original, revised = change["original"]["text"], change["revised"]["text"]
    if max(len(original), len(revised)) <= 200:
        windows = [(0, len(original), 0, len(revised))]
    else:
        windows = []
        for tag, os, oe, rs, re in text_opcodes(original, revised):
            if tag == "equal":
                continue
            window = (max(0, os - 20), min(len(original), oe + 20),
                      max(0, rs - 20), min(len(revised), re + 20))
            if windows and (window[0] <= windows[-1][1] or window[2] <= windows[-1][3]):
                prev = windows.pop()
                window = (prev[0], max(prev[1], window[1]), prev[2], max(prev[3], window[3]))
            windows.append(window)
    return [{side: {"text": text[start:end], "start": change[side]["start"] + start,
                    "end": change[side]["start"] + end, "excerpt": start != 0 or end != len(text)}
             for side, text, start, end in (("original", original, os, oe), ("revised", revised, rs, re))}
            for os, oe, rs, re in windows]


def build_feedback(report: dict, original: str, revised: str, *, title: str, scope: str,
                   selected_ids: list[str] | None = None, include_pending: bool = False,
                   include_context: bool = True, now: str | None = None,
                   include_noted_pending: bool = False) -> dict:
    """派生意见数据，不改报告。scope 无默认值，作者必须选择 local / rewrite。

    默认导出已决定的人工项。显式 selected_ids 是本次选择的 ID 集，不能含重复或未知 ID；
    待定须另开 include_pending，系统自动通过不能冒充作者决定。
    """
    original, revised, _ = checked_source(report, original, revised)
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ReviewDataError("必须明确选择 local（局部修订）或 rewrite（整体改写）")
    if not isinstance(title, str) or not title.strip():
        raise ReviewDataError("文稿名不能为空")
    if type(include_pending) is not bool or type(include_context) is not bool:
        raise ReviewDataError("include_pending 和 include_context 必须是 bool")
    now = now if now is not None else datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            raise ValueError
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReviewDataError("导出时间必须是带时区的 ISO 8601 文本") from exc
    changes = report["changes"]
    known = {change["id"] for change in changes}
    if selected_ids is not None:
        if not isinstance(selected_ids, list) or any(not isinstance(cid, str) for cid in selected_ids):
            raise ReviewDataError("selected_ids 必须是修改 ID 列表")
        if len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= known:
            raise ReviewDataError("selected_ids 含重复或未知的修改 ID")
    selected = known if selected_ids is None else set(selected_ids)
    sections = {"actions": [], "accepted": [], "pending": []}
    sentences = {"original": split_sentences(original), "revised": split_sentences(revised)}
    for change in changes:
        if change["id"] not in selected:
            continue
        decision, status = change["decision"], change["decision"]["status"]
        if status == "auto_passed" and include_noted_pending and decision["note"].strip():
            status = "pending"
        if status == "auto_passed":
            if selected_ids is not None:
                raise ReviewDataError("系统自动通过没有作者决定，不能当作作者修改意见")
            continue
        if status == "pending" and not (include_pending or include_noted_pending and decision["note"].strip()):
            if selected_ids is not None:
                raise ReviewDataError("选择中含待定项；如需导出，请明确启用 include_pending")
            continue
        entry = {"change_id": change["id"], "status": status, "choice": _CHOICES[status],
                 "position": {side: _position(change[side], sentences[side]) for side in sentences},
                 "note": decision["note"]}
        if status == "accepted":
            text = change["revised"]["text"]
            entry["preview"] = {"text": text[:30], "truncated": len(text) > 30}
            entry["request"] = "请保留作者已采用的这一组写法。" if scope == "local" else "这一组是作者认可的写法，供整体改写时参考。"
            sections["accepted"].append(entry)
            continue
        entry["excerpts"] = _excerpts(change)
        entry["evidence"] = change_evidence(change)
        entry["context"] = {side: _previous(change[side], sentences[side]) for side in sentences} if include_context else {}
        if status == "pending":
            entry["request"] = "作者尚未决定，请等待作者确认；下列备注不代表已采用任何候选写法。"
            sections["pending"].append(entry)
        else:
            entry["request"] = "请恢复为以下原稿内容。" if status == "rejected" else "请使用以下作者指定文本。"
            # 完整保留目标文本：长句的定位引文可缩短，但不能把作者要恢复/编辑的内容截掉。
            entry["target_text"] = decision["final_text"]
            sections["actions"].append(entry)
    counts = {key: len(entries) for key, entries in sections.items()}
    return {"feedback_version": "1", "title": title, "exported_at": now, "scope": scope,
            "scope_instruction": SCOPES[scope],
            "source": {"original_sha256": report["document"]["original_sha256"],
                       "revised_sha256": report["document"]["revised_sha256"],
                       "tool_version": report["run"]["tool_version"], "lexicon_version": report["run"]["lexicon_version"]},
            "counts": {**counts, "exported": sum(counts.values()), "total_changes": len(changes)},
            "sections": sections}


def _where(position: dict) -> str:
    location = f"第 {position['paragraph']} 段第 {position['sentence']} 句起" if position["paragraph"] is not None else "空侧定位点"
    return f"{location}，字符 [{position['start']}, {position['end']})"


def _render(data: dict, markdown: bool, *, for_revision: bool = False) -> str:
    def quote(text):
        if not text:
            return "（本侧为空）"
        return _fence(text) if markdown else "\n".join("    " + line for line in text.split("\n"))

    def heading(text, level=2):
        return f"{'#' * level} {text}" if markdown else text

    source, counts = data["source"], data["counts"]
    lines = [heading("作者修改意见", 1), "", heading(f"修订范围 · {SCOPE_LABELS[data['scope']]}"),
             "", data["scope_instruction"], "", "文稿名：", quote(data["title"]), "", "导出时间：",
             quote(data["exported_at"]), "", "工具 / 词表版本：",
             quote(source["tool_version"] + " / " + source["lexicon_version"]), "",
             f"原稿 SHA256：{source['original_sha256']}", f"改稿 SHA256：{source['revised_sha256']}", "",
             "本文件对应上述两版文稿。引文用于定位，长句可能分段摘录。作者指定的恢复或编辑文本完整保留。",
             "用词检查和模型提示供核对。请结合原稿、改稿和作者意见判断。", "",
             f"导出 {counts['exported']} / {counts['total_changes']} 组：需修改 {counts['actions']}，"
             f"作者已采用 {counts['accepted']}，待定 {counts['pending']}。未列出的修改不代表作者已经同意。", ""]
    actions_title = "原有裁决与相关意见" if for_revision else "需要按作者意见处理"
    for key, title in (("actions", actions_title), ("accepted", "作者已采用"), ("pending", "作者尚未决定")):
        if not data["sections"][key]:
            continue
        lines += [heading(title), ""]
        for entry in data["sections"][key]:
            request = entry["request"]
            if for_revision:
                if entry["note"].strip():
                    request = "严格执行下方作者修改建议。建议优先于此处之前的取舍和指定文本；旧文本仅用于定位。"
                elif key == "pending":
                    request = "此处没有作者建议，仅提供上下文。"
                else:
                    request = "此处记录的取舍已反映在作者合并稿，供核对，属于原有裁决。"
            lines += [heading(f"{entry['change_id']} · {entry['choice']}", 3), "",
                      "原稿位置：" + _where(entry["position"]["original"]),
                      "改稿位置：" + _where(entry["position"]["revised"]), "", request, ""]
            if key == "accepted":
                lines += ["改稿开头（仅定位，不是完整文本）：", quote(entry["preview"]["text"]), ""]
            else:
                if "target_text" in entry:
                    if entry["target_text"]:
                        lines += ["完整指定文本：", quote(entry["target_text"]), ""]
                    else:
                        lines += ["指定文本为空：请删除这一组，不插入占位文字。", ""]
                for side, zh in (("original", "原稿"), ("revised", "改稿")):
                    context = entry["context"].get(side)
                    if context:
                        lines += [f"{zh}同段前一句（上下文）：", quote(context["text"]), ""]
                for excerpt in entry["excerpts"]:
                    for side, zh in (("original", "原句"), ("revised", "改句")):
                        value = excerpt[side]
                        kind = "摘录" if value["excerpt"] else "全文"
                        lines += [f"{zh}（{kind}，字符 [{value['start']}, {value['end']})）：", quote(value["text"]), ""]
                for field, zh in (("observed", "文字变化"), ("rules", "用词检查提示"), ("model", "模型提示（判断）")):
                    if entry["evidence"][field]:
                        lines += [zh + "：", quote("\n".join(entry["evidence"][field])), ""]
            if entry["note"]:
                lines += ["作者备注（原文）：", quote(entry["note"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def feedback_markdown(report: dict, original: str, revised: str, *, for_revision: bool = False, **options) -> str:
    """以 Markdown 呈现意见。生成候选稿时，附上的待定备注可用来提出候选改法。"""
    return _render(build_feedback(report, original, revised, include_noted_pending=for_revision, **options),
                   True, for_revision=for_revision)


def feedback_text(report: dict, original: str, revised: str, **options) -> str:
    """build_feedback() 的相同参数，输出以缩进区分引用内容的纯文本。"""
    return _render(build_feedback(report, original, revised, **options), False)
