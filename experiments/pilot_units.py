"""试标的只读审阅单元视图；原始 AuditReport 和逐条自检映射保持不变。"""

from __future__ import annotations

from acceptance_desk._schema import report_digest as digest
from acceptance_desk.text import split_sentences

if __package__:
    from .report_checks import check_source_report
else:
    from report_checks import check_source_report

UNIT_VERSION = "review_unit_v1"


def measurement_view(report: dict, documents: dict[str, str], units: list[dict]) -> dict:
    """核对完整分区后派生测量行。组内可不相邻，文字按片段换行连接，不伪装成连续原文。"""
    check_source_report(report, documents["original"], documents["revised"])
    changes = {c["id"]: c for c in report["changes"]}
    order = {cid: n for n, cid in enumerate(changes)}
    if not isinstance(units, list) or not units:
        raise ValueError("units 必须是非空的完整分区列表")
    seen, unit_ids, rows = set(), set(), []
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("单元必须是包含 id 与 change_ids 的对象")
        uid, ids = unit.get("id"), unit.get("change_ids")
        if not isinstance(uid, str) or not uid.strip() or uid != uid.strip() or uid in unit_ids:
            raise ValueError("单元 id 为空、重复或带首尾空白")
        if not isinstance(ids, list) or not ids or any(not isinstance(cid, str) for cid in ids):
            raise ValueError(f"{uid} 的 change_ids 必须为非空编号列表")
        if len(set(ids)) != len(ids) or not set(ids) <= set(changes) or set(ids) & seen:
            raise ValueError(f"{uid} 包含未知、重复或跨单元重复的修改编号")
        if ids != sorted(ids, key=order.get):
            raise ValueError(f"{uid} 的修改编号未按原报告顺序排列")
        seen.update(ids)
        unit_ids.add(uid)
        members = [changes[cid] for cid in ids]
        row = {"id": uid, "change_ids": ids,
               "review": {"flagged": any(c["review"]["flagged"] for c in members),
                          "reasons": [r for c in members for r in c["review"]["reasons"]]}}
        for side in documents:
            fragments = [c[side] for c in members if c[side]["text"]]
            # 连续片段保留原有空白；不相邻片段明确用省略标记隔开。
            text = ""
            for previous, fragment in zip([None, *fragments], fragments):
                if previous is not None:
                    gap = documents[side][previous["end"]:fragment["start"]]
                    text += gap if not gap.strip() else "\n[…其他单元…]\n"
                text += fragment["text"]
            row[side] = {"text": text, "start": min(c[side]["start"] for c in members),
                         "end": max(c[side]["end"] for c in members)}
        rows.append(row)
    if seen != set(changes):
        raise ValueError("单元分区必须覆盖报告中全部修改，不能只传已抽样的单元")
    firsts = [order[row["change_ids"][0]] for row in rows]
    if firsts != sorted(firsts):
        raise ValueError("单元必须按首条修改的位置排列")
    return {**report, "changes": rows}


def check_unit_key(report: dict, key: dict, documents: dict[str, str]) -> dict:
    if key.get("unit_version") != UNIT_VERSION or key.get("report_sha256") != digest(report):
        raise ValueError("单元钥匙版本或报告指纹不一致")
    if key.get("sampling_design") != "stratified_random":
        raise ValueError("只接受已登记的单元分层随机抽样，不把手选单元当随机样本")
    view = measurement_view(report, documents, key["units"])
    if type(key.get("total_raw_changes")) is not int or key["total_raw_changes"] != len(report["changes"]):
        raise ValueError("原始修改总数与单元钥匙不一致")
    expected_counts = {
        "original_sentences": len(split_sentences(documents["original"])),
        "changed_original_sentences": sum({"1-1": 1, "1-2": 1, "2-1": 2, "1-0": 1, "0-1": 0}[c["alignment"]]
                                          for c in report["changes"]),
    }
    if any(type(key.get(k)) is not int or key[k] != v for k, v in expected_counts.items()):
        raise ValueError("原稿句子规模与单元钥匙不一致")
    members = {u["id"]: u["change_ids"] for u in key["units"]}
    for uid, item in key["items"].items():
        if uid not in members or item.get("change_ids") != members[uid]:
            raise ValueError(f"{uid} 的抽样成员与完整单元分区不一致")
    return view


def unit_hits(hits: set[str], units: list[dict]) -> set[str]:
    """原始修改命中归到所属单元；同一单元命中多次只计一次。"""
    return {u["id"] for u in units if hits.intersection(u["change_ids"])}
