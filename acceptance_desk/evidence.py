"""无需 UI 的检查依据：观察到的变化、规则命中、模型提示分开返回。"""

from __future__ import annotations

from difflib import SequenceMatcher
import re

from .decisions import ReviewDataError

_TOKEN = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*%?|[A-Za-z]+(?:['’][A-Za-z]+)*|\s+|.", re.S)
NLI_MESSAGES = {
    "nli_not_entailed": "语义模型提示：从原句可能推不出改句的全部内容（模型判断，可能有误）",
    "nli_contradiction": "语义模型提示：两句可能存在矛盾（模型判断，可能有误）",
}


def text_opcodes(original: str, revised: str) -> list[tuple[str, int, int, int, int]]:
    """数字、拉丁词、空白作为整体，其余按字符比较，返回文本位置。"""
    left, right = list(_TOKEN.finditer(original)), list(_TOKEN.finditer(revised))
    out = []
    for tag, a, b, c, d in SequenceMatcher(None, [m.group() for m in left], [m.group() for m in right],
                                          autojunk=False).get_opcodes():
        os = left[a].start() if a < len(left) else len(original)
        oe = left[b - 1].end() if b > a else os
        rs = right[c].start() if c < len(right) else len(revised)
        re_ = right[d - 1].end() if d > c else rs
        out.append((tag, os, oe, rs, re_))
    return out


def paired_text_changes(alignment: str, original: str, revised: str, limit: int = 4) -> list[str]:
    """列出两侧文本的拆并和字词变化，按显示上限汇总余项。"""
    observed = []
    if alignment in ("1-2", "2-1"):
        observed.append("一句拆成了两句" if alignment == "1-2" else "两句合成了一句")
    operations = [op for op in text_opcodes(original, revised) if op[0] != "equal"]

    def clip(text):
        text = text.replace("\n", "⏎")
        return text if len(text) <= 18 else text[:18] + "…"

    for tag, os, oe, rs, re_ in operations[:limit]:
        a, b = original[os:oe], revised[rs:re_]
        if not a.strip() and not b.strip():
            observed.append("空白有变化")
        elif tag == "delete":
            observed.append(f"删去“{clip(a)}”")
        elif tag == "insert":
            observed.append(f"新增“{clip(b)}”")
        else:
            observed.append(f"“{clip(a)}”改为“{clip(b)}”")
    if len(operations) > limit:
        observed.append(f"另有 {len(operations) - limit} 处字词变化")
    return observed


def change_evidence(change: dict) -> dict[str, list[str]]:
    """接受已校验的修改项；不把推断、规则或模型概率写成观察事实。"""
    alignment = change["alignment"]
    original, revised = change["original"]["text"], change["revised"]["text"]
    if alignment in ("0-1", "1-0"):
        observed = ["改稿新增了这一句" if alignment == "0-1" else "改稿删去了这一句"]
    else:
        observed = paired_text_changes(alignment, original, revised)
        features = change.get("features", {})
        rule_features = features.get("rules", {}) if isinstance(features, dict) else {}
        if not isinstance(rule_features, dict):
            raise ReviewDataError("规则特征格式不正确")
        added, removed = rule_features.get("numbers_added", []), rule_features.get("numbers_removed", [])
        if any(not isinstance(values, list) or any(not isinstance(v, str) for v in values) for values in (added, removed)):
            raise ReviewDataError("数字证据应为文本列表")
        if added and removed:
            observed.append(f"数字从 {'、'.join(removed)} 变为 {'、'.join(added)}")
    rules, model = [], []
    for reason in change["review"]["reasons"]:
        if not isinstance(reason, dict) or any(not isinstance(reason.get(k), str) for k in ("source", "code", "detail")):
            raise ReviewDataError("检查依据格式不正确")
        if reason["source"] == "rule":
            rules.append(reason["detail"])
        elif reason["source"] == "nli":
            model.append(NLI_MESSAGES.get(reason["code"], "语义模型给出其他提示（模型判断，可能有误）"))
        elif reason["source"] != "alignment":
            raise ReviewDataError("检查依据来源不受支持")
    return {"observed": observed or ["文本有变化"], "rules": rules, "model": model}
