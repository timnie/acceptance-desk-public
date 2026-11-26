"""audit() 与 validate_report()（schema §1、§3、§9）。"""

from __future__ import annotations

import math
import warnings
from collections import Counter
from datetime import datetime

from . import __version__
from ._schema import ALIGNMENTS, SCHEMA_VERSION, is_integer, numbered_id, text_sha256
from .align import Side, align
from .lexicon import categories_for, load_lexicon
from .models import FeatureModels, ModelUnavailable
from .review import DEFAULT_REVIEW_CONFIG, candidate_reasons
from .rules import rule_features
from .text import normalize, split_paragraphs

_EMPTY_CLASSIFICATION = {"status": "untrained", "label": None, "label_zh": None,
                         "probabilities": None, "risk_probability": None}
_LIST_FIELDS = {  # 展开列表 → (类别, 删去为 -1 / 新增为 +1)
    "hedge_removed": ("qualifiers", -1), "hedge_added": ("qualifiers", +1),
    "certainty_added": ("certainty", +1), "certainty_removed": ("certainty", -1),
    "connective_added": ("connective", +1), "connective_removed": ("connective", -1),
    "defensive_added": ("defensive", +1), "slop_added": ("slop", +1),
    "attribution_added": ("attribution", +1),
}


def _side_dict(s: Side) -> dict:
    return {"text": s.text, "start": s.start, "end": s.end, "paragraph": s.paragraph}


def _resolve_models(mode: str, model_cache):
    """返回 (实际模式, FeatureModels | None)。"""
    if mode == "rules_only":
        return "rules_only", None
    if mode not in ("auto", "full"):
        raise ValueError(f"mode 只能是 auto / full / rules_only，收到 {mode!r}")
    try:
        return "full", FeatureModels.load(model_cache)
    except ModelUnavailable as e:
        if mode == "full":
            raise RuntimeError(f"mode='full' 但模型不可用：{e}") from e
        warnings.warn(f"模型不可用，退回 rules_only：{e}", RuntimeWarning, stacklevel=3)
        return "rules_only", None


def audit(original: str, revised: str, *, mode: str = "auto",
          lexicon_path: str | None = None, model_cache: str | None = None) -> dict:
    """返回 AuditReport（schema §3），可直接 json.dumps(report, ensure_ascii=False)。"""
    original, revised = normalize(original), normalize(revised)
    lexicon = load_lexicon(lexicon_path)
    actual_mode, models = _resolve_models(mode, model_cache)
    review_config = dict(DEFAULT_REVIEW_CONFIG)

    changes = []
    for k, pair in enumerate(align(original, revised), 1):
        features = {"rules": rule_features(pair.original, pair.revised, lexicon), "nli": None, "similarity": None}
        if models is not None and pair.alignment not in ("0-1", "1-0"):
            o, r = pair.original.text, pair.revised.text
            features["nli"] = {"forward": models.nli(o, r), "backward": models.nli(r, o)}
            features["similarity"] = models.similarity(o, r)
        reasons = candidate_reasons(pair.alignment, features, review_config)
        changes.append({
            "id": None,  # 排序后统一编号
            "alignment": pair.alignment,
            "original": _side_dict(pair.original),
            "revised": _side_dict(pair.revised),
            "features": features,
            "classification": dict(_EMPTY_CLASSIFICATION),
            "review": {"flagged": bool(reasons), "needs_review": True, "basis": "untrained", "reasons": reasons},
            "decision": {"status": "pending", "final_text": None, "decided_by": None, "decided_at": None, "note": ""},
        })
    for k, c in enumerate(changes, 1):
        c["id"] = numbered_id("c", k, len(changes))

    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "tool_version": __version__,
            "mode": actual_mode,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "models": models.info if models is not None else None,
            "lexicon_version": lexicon.version,
            "classifier": {"status": "untrained", "name": None, "threshold": None},
            "review_config": review_config,
        },
        "document": {
            "original_sha256": text_sha256(original),
            "revised_sha256": text_sha256(revised),
            "original_chars": len(original),
            "revised_chars": len(revised),
            "original_paragraphs": len(split_paragraphs(original)),
            "revised_paragraphs": len(split_paragraphs(revised)),
        },
        "summary": _summary(changes),
        "changes": changes,
    }


def _summary(changes: list[dict]) -> dict:
    flagged = sum(c["review"]["flagged"] for c in changes)
    needs = sum(c["review"]["needs_review"] for c in changes)
    by_reason = Counter(code for c in changes for code in {r["code"] for r in c["review"]["reasons"]})
    trained = any(c["classification"]["status"] == "trained" for c in changes)
    return {
        "total_changes": len(changes),
        "flagged": flagged,
        "unflagged": len(changes) - flagged,
        "needs_review": needs,
        "auto_passed": len(changes) - needs,
        "by_alignment": {a: sum(c["alignment"] == a for c in changes) for a in ALIGNMENTS},
        "by_reason": dict(sorted(by_reason.items())),
        "by_label": None if not trained else dict(Counter(c["classification"]["label"] for c in changes)),
    }


# ---------------------------------------------------------------- 校验

_NLI_KEYS = {"entailment", "neutral", "contradiction"}


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _span_error(obj: dict, length: int) -> str | None:
    """start / end 必须是非 bool 整数，且 0 <= start <= end <= length。返回错误说明或 None。"""
    s, e = obj.get("start"), obj.get("end")
    if not (is_integer(s) and is_integer(e)):
        return f"start/end 必须是整数（不能是 bool），实际为 {s!r}/{e!r}"
    if not 0 <= s <= e <= length:
        return f"区间 [{s},{e}) 越界，应满足 0 <= start <= end <= {length}"
    return None


def _prob_errors(p, where: str) -> list[str]:
    if not isinstance(p, dict) or set(p) != _NLI_KEYS:
        return [f"{where} 的键应为 entailment/neutral/contradiction，实际为 {sorted(p) if isinstance(p, dict) else p!r}"]
    errs = [f"{where}.{k} 应为 [0, 1] 内的有限数，实际为 {v!r}"
            for k, v in p.items() if not (_is_num(v) and 0 <= v <= 1)]
    if not errs and abs(sum(p.values()) - 1) > 0.001:
        errs.append(f"{where} 概率和不为 1")
    return errs

def validate_report(report: dict, original: str, revised: str) -> list[str]:
    """按 schema §9 逐条检查，返回错误信息列表；空列表表示通过。original / revised 需先规范化。"""
    errs: list[str] = []
    full = {"original": original, "revised": revised}
    run = report.get("run", {})
    changes = report.get("changes", [])
    untrained = run.get("classifier", {}).get("status") == "untrained"
    version = str(run.get("lexicon_version", ""))
    cats = categories_for(version)  # 按报告记录的词表版本校验，旧报告不因新增类别失效
    rules_only = run.get("mode") == "rules_only"

    if report.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version 应为 {SCHEMA_VERSION}")
    doc = report.get("document", {})
    for side in ("original", "revised"):
        if doc.get(f"{side}_sha256") != text_sha256(full[side]):
            errs.append(f"document.{side}_sha256 与全文不符")
        if doc.get(f"{side}_chars") != len(full[side]):
            errs.append(f"document.{side}_chars 与全文不符")
        if doc.get(f"{side}_paragraphs") != len(split_paragraphs(full[side])):
            errs.append(f"document.{side}_paragraphs 与全文不符")

    # 1. 先检查所有区间合法，再做排序和切片（非法位置不参与后续比较）
    span_ok = {}
    for c in changes:
        for side in ("original", "revised"):
            err = _span_error(c[side], len(full[side]))
            span_ok[(c.get("id"), side)] = err is None
            if err:
                errs.append(f"{c.get('id')}: {side} {err}")

    # 2. 排序与编号
    if not all(span_ok.values()):
        keys = []
    else:
        keys = [(c["original"]["start"], c["original"]["end"], c["revised"]["start"], c["revised"]["end"]) for c in changes]
    if keys != sorted(keys):
        errs.append("changes 未按 (original.start, original.end, revised.start, revised.end) 排序")
    for k, c in enumerate(changes, 1):
        expected_id = numbered_id("c", k, len(changes))
        if c.get("id") != expected_id:
            errs.append(f"第 {k} 条的 id 应为 {expected_id}，实际 {c.get('id')}")

    for c in changes:
        cid, al = c.get("id"), c.get("alignment")
        if al not in ALIGNMENTS:
            errs.append(f"{cid}: 未知对齐类型 {al}")
        # 1. 区间切片复原
        for side in ("original", "revised"):
            s = c[side]
            if not span_ok[(cid, side)]:
                continue
            if full[side][s["start"]:s["end"]] != s["text"]:
                errs.append(f"{cid}: {side} 区间 [{s['start']},{s['end']}) 切片与 text 不符")
            if s["text"] == "" and s["start"] != s["end"]:
                errs.append(f"{cid}: {side} 为空侧但区间不是零宽")
        if al == "0-1" and c["original"]["text"] != "":
            errs.append(f"{cid}: 0-1 的原稿侧应为空")
        if al == "1-0" and c["revised"]["text"] != "":
            errs.append(f"{cid}: 1-0 的改稿侧应为空")
        if c["original"]["text"] == c["revised"]["text"]:
            errs.append(f"{cid}: 两侧文本完全相同，不应进 changes")

        feats = c["features"]
        rules = feats["rules"]
        # 3. 命中位置
        counts = {side: Counter() for side in ("original", "revised")}
        for h in rules["hits"]:
            if h.get("side") not in full:
                errs.append(f"{cid}: 命中 {h.get('term')} 的 side 不合法：{h.get('side')!r}")
                continue
            s = c[h["side"]]
            counts[h["side"]][(h["category"], h["term"])] += 1
            if h["category"] not in cats:
                errs.append(f"{cid}: 命中类别 {h['category']} 不合法")
            err = _span_error(h, len(full[h["side"]]))
            if err:
                errs.append(f"{cid}: 命中 {h['term']} {err}")
                continue
            if full[h["side"]][h["start"]:h["end"]] != h["text"]:
                errs.append(f"{cid}: 命中 {h['term']} 的切片与 text 不符")
            if span_ok[(cid, h["side"])] and not (s["start"] <= h["start"] <= h["end"] <= s["end"]):
                errs.append(f"{cid}: 命中 {h['term']} 不在 {h['side']} 区间内")
        # 4. delta 与 hits、展开列表一致
        if set(rules["delta"]) != set(cats):
            errs.append(f"{cid}: delta 应包含词表 {version} 的 {len(cats)} 个类别")
        for cat in cats:
            terms = {t for (ct, t) in counts["original"] | counts["revised"] if ct == cat}
            expect = {t: counts["revised"][(cat, t)] - counts["original"][(cat, t)] for t in terms}
            expect = {t: v for t, v in expect.items() if v}
            if rules["delta"].get(cat) != expect:
                errs.append(f"{cid}: delta[{cat}] 与 hits 计数不符")
        for key, (cat, sign) in _LIST_FIELDS.items():
            if cat not in cats:
                if key in rules:
                    errs.append(f"{cid}: 词表 {version} 没有 {cat} 类别，不应有 {key}")
                continue
            if key not in rules:
                errs.append(f"{cid}: 词表 {version} 的报告缺少 {key}")
                continue
            want = Counter({t: v * sign for t, v in rules["delta"].get(cat, {}).items() if v * sign > 0})
            if Counter(rules[key]) != want:
                errs.append(f"{cid}: {key} 与 delta 不一致")

        both = al not in ("0-1", "1-0")
        if not both and (feats["nli"] is not None or feats["similarity"] is not None):
            errs.append(f"{cid}: 新增 / 删除时 nli 与 similarity 应为 null")
        if not both and (rules["length_ratio"] is not None or rules["char_edit_ratio"] is not None):
            errs.append(f"{cid}: 新增 / 删除时比例类特征应为 null")
        nli = feats["nli"]
        if nli is not None:
            if not isinstance(nli, dict) or set(nli) != {"forward", "backward"}:
                errs.append(f"{cid}: nli 应为 {{forward, backward}}")
            else:
                for d in ("forward", "backward"):
                    errs.extend(f"{cid}: {m}" for m in _prob_errors(nli[d], f"nli.{d}"))
        sim = feats["similarity"]
        if sim is not None and not (_is_num(sim) and -1 <= sim <= 1):
            errs.append(f"{cid}: similarity 应为 [-1, 1] 内的有限数，实际为 {sim!r}")

        rv, cl, dec = c["review"], c["classification"], c["decision"]
        # 6. 未训练时 classification 全为 null
        if untrained:
            if cl.get("status") != "untrained" or any(cl.get(k) is not None for k in
                                                      ("label", "label_zh", "probabilities", "risk_probability")):
                errs.append(f"{cid}: 未训练时 classification 各值应为 null")
        # 7. flagged 与 needs_review
        if rv["flagged"] != (len(rv["reasons"]) > 0):
            errs.append(f"{cid}: flagged 应等于 len(reasons) > 0")
        if untrained:
            if not rv["needs_review"] or rv["basis"] != "untrained" or dec["status"] != "pending":
                errs.append(f"{cid}: 未训练时应 needs_review=true、basis=untrained、decision=pending")
        elif not rv["needs_review"] and dec["status"] != "auto_passed":
            errs.append(f"{cid}: needs_review=false 的条目应为 auto_passed")
        # 8. rules_only
        if rules_only:
            if feats["nli"] is not None or feats["similarity"] is not None:
                errs.append(f"{cid}: rules_only 时 nli / similarity 应为 null")
            if any(r["source"] == "nli" for r in rv["reasons"]):
                errs.append(f"{cid}: rules_only 时不应有 nli 来源的原因")

    if rules_only and run.get("models") is not None:
        errs.append("rules_only 时 run.models 应为 null")
    if untrained and report.get("summary", {}).get("by_label") is not None:
        errs.append("未训练时 summary.by_label 应为 null")
    # 5. summary 与 changes 重算一致
    if report.get("summary") != _summary(changes):
        errs.append("summary 与 changes 重算结果不一致")
    return errs
