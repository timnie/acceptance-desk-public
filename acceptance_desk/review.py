"""候选原因（schema §6）。

每条原因注明来源（alignment / rule / nli），detail 由固定模板生成，不由模型写文字。
原因只决定 review.flagged（重点审阅 / 其余修改），不决定是否自动通过。
"""

from __future__ import annotations

DEFAULT_REVIEW_CONFIG = {"nli_entailment_min": 0.5, "nli_contradiction_min": 0.5, "tuned": False}

# (code, rules 里的字段, detail 前缀)，顺序即 §6 表格顺序 3–13
RULE_LIST_REASONS = (
    ("hedge_removed", "hedge_removed", "删去限定词"),
    ("hedge_added", "hedge_added", "新增限定词"),
    ("certainty_added", "certainty_added", "新增确定性词"),
    ("connective_added", "connective_added", "新增推论连接词"),
    ("defensive_added", "defensive_added", "新增防御性表述"),
    ("slop_added", "slop_added", "新增套话"),
    ("number_added", "numbers_added", "新增数字"),
    ("number_removed", "numbers_removed", "删去数字"),
    ("citation_added", "citations_added", "新增引用"),
    ("citation_removed", "citations_removed", "删去引用"),
    ("attribution_added", "attribution_added", "新增归因"),  # 词表 0.2 起；旧报告没有这个字段
)


def _quote(terms: list[str]) -> str:
    """“可能”“直接”；同一词条出现多次时写作“可能”×2。"""
    parts = []
    for t in dict.fromkeys(terms):
        n = terms.count(t)
        parts.append(f"“{t}”" + (f"×{n}" if n > 1 else ""))
    return "".join(parts)


def _reason(source: str, code: str, detail: str, terms: list[str] | None = None) -> dict:
    return {"source": source, "code": code, "detail": detail, "terms": list(terms or [])}


def candidate_reasons(alignment: str, features: dict, review_config: dict) -> list[dict]:
    reasons = []
    if alignment == "0-1":
        reasons.append(_reason("alignment", "sentence_inserted", "新增整句"))
    if alignment == "1-0":
        reasons.append(_reason("alignment", "sentence_deleted", "删除整句"))

    rules = features["rules"]
    for code, key, prefix in RULE_LIST_REASONS:
        terms = rules.get(key, [])
        if terms:
            reasons.append(_reason("rule", code, prefix + _quote(terms), terms))
    if rules["causal_upgrade"]:
        reasons.append(_reason("rule", "causal_upgrade", "相关类表述减少、因果谓词增加"))

    nli = features.get("nli")
    if nli is not None:
        fwd, bwd = nli["forward"], nli["backward"]
        if fwd["entailment"] < review_config["nli_entailment_min"]:
            reasons.append(_reason("nli", "nli_not_entailed",
                                   "原句不蕴含改句（原→改 蕴含概率低于 nli_entailment_min）"))
        if max(fwd["contradiction"], bwd["contradiction"]) >= review_config["nli_contradiction_min"]:
            reasons.append(_reason("nli", "nli_contradiction",
                                   "两句存在矛盾信号（任一方向矛盾概率不低于 nli_contradiction_min）"))
    return reasons
