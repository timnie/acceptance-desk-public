"""已审阅记录的校验（schema v0.1 §4.2）。合并终稿和审阅页导入共用这一份规则。

两种报告、两种校验：
- audit() 刚输出的原始报告：用 report.validate_report() 按 §9 检查（未训练时每条都必须是 pending）。
- 人已经回填过 decision 的“已审阅记录”（进度文件、合并输入）：用本模块。
  它检查结构、位置和每条决定是否合法，但不要求 pending。

所有问题都以中文消息的 ReviewDataError 报出，不让 KeyError / TypeError 之类的异常漏到调用方。
"""

from __future__ import annotations

from datetime import datetime

from ._schema import ALIGNMENTS, is_integer, text_sha256

DECISION_STATUSES = ("pending", "accepted", "rejected", "edited", "auto_passed")
USER_STATUSES = ("pending", "accepted", "rejected", "edited")
STATUS_ZH = {"accepted": "采用改句", "rejected": "保留原句", "edited": "自行编辑", "auto_passed": "自动通过",
             "pending": "待定"}


class ReviewDataError(ValueError):
    """已审阅记录不合法。消息为中文，可直接展示给用户。"""


def _is_time(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        datetime.fromisoformat(v)
    except ValueError:
        return False
    return True


def is_trained(report: dict) -> bool:
    run = report.get("run") if isinstance(report, dict) else None
    clf = run.get("classifier") if isinstance(run, dict) else None
    return isinstance(clf, dict) and clf.get("status") == "trained"


def decision_errors(change: dict, *, trained: bool, created_at: str | None = None) -> list[str]:
    """一条修改的 decision 是否合法（§4.2）。trained 为报告的分类器是否已训练。"""
    cid = change.get("id") if isinstance(change, dict) else None
    dec = change.get("decision") if isinstance(change, dict) else None
    if not isinstance(dec, dict):
        return [f"{cid}: 缺少 decision 或格式不对"]
    st, ft, by, at = dec.get("status"), dec.get("final_text"), dec.get("decided_by"), dec.get("decided_at")
    errs: list[str] = []
    if not isinstance(dec.get("note"), str):
        errs.append(f"{cid}: 备注应为文本")
    if st not in DECISION_STATUSES:
        return errs + [f"{cid}: 未知的决定状态 {st!r}"]
    if st == "pending":
        if ft is not None or by is not None or at is not None:
            errs.append(f"{cid}: 待定条目不应有最终文本、决定人或决定时间")
        return errs
    orig = change.get("original", {}).get("text") if isinstance(change.get("original"), dict) else None
    rev = change.get("revised", {}).get("text") if isinstance(change.get("revised"), dict) else None
    if not isinstance(ft, str):
        errs.append(f"{cid}: 已决定的条目缺少最终文本")
    elif st in ("accepted", "auto_passed") and ft != rev:
        errs.append(f"{cid}: 状态为{STATUS_ZH[st]}，但最终文本与改句不一致")
    elif st == "rejected" and ft != orig:
        errs.append(f"{cid}: 状态为保留原句，但最终文本与原句不一致")
    if st == "auto_passed":
        review = change.get("review") if isinstance(change.get("review"), dict) else {}
        if not trained:
            errs.append(f"{cid}: 分类器尚未训练，不能有“自动通过”的条目（未经确认的修改不能算系统已通过）")
        elif review.get("needs_review") is not False:
            errs.append(f"{cid}: 只有 needs_review 为 false 的条目可以自动通过")
        if by != "system":
            errs.append(f"{cid}: 自动通过的决定人应为 system")
        if created_at is not None and at != created_at:
            errs.append(f"{cid}: 自动通过的决定时间应等于本次检查的时间")
    else:
        if by != "user":
            errs.append(f"{cid}: 人工决定的决定人应为 user，实际为 {by!r}")
        if not _is_time(at):
            errs.append(f"{cid}: 缺少决定时间，或时间格式不对（应为 ISO 8601）")
    return errs


def reviewed_report_errors(report, original: str, revised: str) -> list[str]:
    """已审阅记录的完整检查：结构、文稿哈希、编号、位置与文本、决定。original / revised 需已规范化。"""
    if not isinstance(report, dict):
        return ["记录不是 JSON 对象"]
    doc, changes = report.get("document"), report.get("changes")
    if not isinstance(doc, dict) or not isinstance(changes, list):
        return ["记录缺少 document 或 changes"]
    errs: list[str] = []
    for side, zh, full in (("original", "原稿", original), ("revised", "改稿", revised)):
        if doc.get(f"{side}_sha256") != text_sha256(full):
            errs.append(f"记录对应的{zh}与传入的{zh}不是同一份（内容哈希不一致）")
    if errs:
        return errs
    trained = is_trained(report)
    run = report.get("run") if isinstance(report.get("run"), dict) else {}
    created_at = run.get("created_at")
    seen: set = set()
    full = {"original": original, "revised": revised}
    for k, c in enumerate(changes, 1):
        if not isinstance(c, dict):
            errs.append(f"第 {k} 条不是对象")
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            errs.append(f"第 {k} 条缺少编号")
            cid = f"第 {k} 条"
        elif cid in seen:
            errs.append(f"{cid}: 编号重复")
        seen.add(cid)
        if c.get("alignment") not in ALIGNMENTS:
            errs.append(f"{cid}: 未知的对齐类型 {c.get('alignment')!r}")
        for side, zh in (("original", "原句"), ("revised", "改句")):
            s = c.get(side)
            if not isinstance(s, dict) or not isinstance(s.get("text"), str):
                errs.append(f"{cid}: {zh}缺少文本")
                continue
            a, b = s.get("start"), s.get("end")
            if not (is_integer(a) and is_integer(b)):
                errs.append(f"{cid}: {zh}位置必须是整数（不能是 true/false），实际为 {a!r}/{b!r}")
            elif not 0 <= a <= b <= len(full[side]):
                errs.append(f"{cid}: {zh}位置 [{a},{b}) 越界")
            elif full[side][a:b] != s["text"]:
                errs.append(f"{cid}: {zh}位置与文本不符")
        rv = c.get("review")
        if not isinstance(rv, dict) or not isinstance(rv.get("reasons"), list) or not isinstance(rv.get("flagged"), bool):
            errs.append(f"{cid}: 缺少 review 信息")
        errs.extend(decision_errors(c, trained=trained, created_at=created_at))
    return errs


def check_reviewed_report(report, original: str, revised: str) -> None:
    errs = reviewed_report_errors(report, original, revised)
    if errs:
        raise ReviewDataError("记录未通过检查：" + "；".join(errs[:5]) + (f"；另有 {len(errs) - 5} 处问题" if len(errs) > 5 else ""))
