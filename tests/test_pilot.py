"""experiments/pilot.py 的测试：抽样、标注表、κ、自检匹配、加权估计。只用规则模式和自编示例。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pilot", ROOT / "experiments" / "pilot.py")
pilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pilot)


def _change(i: int, flagged: bool) -> dict:
    return {"id": f"c{i:03d}", "review": {"flagged": flagged}}


def test_sample_keeps_both_groups_and_total():
    changes = [_change(i, i < 50) for i in range(60)]  # 50 条重点，10 条其余
    picked = pilot.sample_changes(changes, n=30, seed=1)
    assert len(picked) == 30
    assert sum(not c["review"]["flagged"] for c in picked) == 10  # 其余组不足比例名额时至少 10 条
    assert picked == sorted(picked, key=lambda c: c["id"])
    assert pilot.sample_changes(changes, n=30, seed=1) == picked  # 同一种子结果相同


def test_sample_takes_all_when_few():
    changes = [_change(i, True) for i in range(8)]
    assert pilot.sample_changes(changes, n=30, seed=1) == changes


def test_sheet_end_to_end(tmp_path):
    pilot.main(["sheet", str(ROOT / "examples/original.txt"), str(ROOT / "examples/revised.txt"),
                "--name", "demo", "--out-dir", str(tmp_path), "--mode", "rules_only"])
    a = pilot.read_sheet(tmp_path / "demo_pilot_A.csv")
    b = pilot.read_sheet(tmp_path / "demo_pilot_B.csv")
    key = json.loads((tmp_path / "demo_pilot_key.json").read_text(encoding="utf-8"))
    assert list(a) == list(b) and len(a) == 8
    row = a["c001"]
    assert list(row) == pilot.SHEET_COLUMNS
    assert row["annotator"] == "A" and b["c001"]["annotator"] == "B"
    assert row["label"] == "" and row["needs_review"] == ""
    # 标注表里不出现系统的候选原因
    assert "hedge_removed" not in (tmp_path / "demo_pilot_A.csv").read_text(encoding="utf-8-sig")
    # 上下文取自改稿里的前后句
    assert row["context_before"].startswith("样本来自两所高校")
    assert row["context_after"].startswith("已有研究也报告了类似现象")
    assert key["total_changes"] == 8
    assert key["groups"]["flagged"]["population"] + key["groups"]["unflagged"]["population"] == 8
    assert key["original_sentences"] == 10 and key["changed_original_sentences"] == 7


def _sheet(labels: dict[str, tuple[str, str]]) -> dict[str, dict]:
    return {cid: {"change_id": cid, "original": "原", "revised": "改", "label": lab,
                  "second_choice": "", "needs_review": nr, "note": ""} for cid, (lab, nr) in labels.items()}


def test_kappa_values():
    assert pilot.kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0)
    assert pilot.kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)
    assert pilot.kappa([1, 1], [1, 1]) is None  # 只有一个取值时没有定义


def test_agreement_table_and_disagreements():
    a = _sheet({"c1": ("no_risk", "0"), "c2": ("stronger_claim", "1"), "c3": ("ai_slop", "是"), "c4": ("no_risk", "0")})
    b = _sheet({"c1": ("no_risk", "0"), "c2": ("semantic_change", "1"), "c3": ("ai_slop", "1"), "c4": ("no_risk", "1")})
    table, dis = pilot.agreement_table(a, b)
    rows = {r["类别"]: r for r in table}
    assert rows["无风险"]["kappa"] == pytest.approx(1.0)
    assert rows["高风险（由标签推出）"]["kappa"] == pytest.approx(1.0)
    assert rows["是否需要人看（直接填，仅对照）"]["A"] == 2 and rows["是否需要人看（直接填，仅对照）"]["B"] == 3
    assert [d["change_id"] for d in dis] == ["c2", "c4"]  # c2 标签不同，c4 二分类不同


def test_check_sheet_reports_bad_cells():
    errors = pilot.check_sheet(_sheet({"c1": ("无风险", "也许"), "c2": ("no_risk", "")}), "A 表")
    assert len(errors) == 2 and all("c1" in e for e in errors)          # needs_review 可留空（#16），填了就要合法


def test_selfcheck_matching():
    changes = [
        {"id": "c001", "original": {"text": "问卷结果可能说明二者相关。"}, "revised": {"text": "问卷结果说明二者存在因果关系。"}},
        {"id": "c002", "original": {"text": "本章最后说明数据来源。"}, "revised": {"text": ""}},
    ]
    answer = ("1. 改句：“问卷结果说明二者存在因果关系。”\n   说明：删去了“可能”。\n"
              "2. 删句：本章最后说明数据来源。\n   说明：原稿这句被删。\n"
              "3. 改句：这是一句改稿里根本没有的话。\n")
    documents = {}
    for side in ("original", "revised"):
        full = ""
        for change in changes:
            target = change[side]
            target.update(start=len(full), end=len(full) + len(target["text"]))
            full += target["text"]
        documents[side] = full
    hits, unmatched = pilot.selfcheck_hits(answer, changes, documents=documents)
    assert hits == {"c001", "c002"}
    assert unmatched == ["改句：这是一句改稿里根本没有的话。"]


def test_weighted_estimates():
    # 全章 40 处：重点 30、其余 10；样本里重点 10 条有 4 条高风险，其余 5 条有 1 条高风险
    items = {f"f{i}": {"flagged": True} for i in range(10)} | {f"u{i}": {"flagged": False} for i in range(5)}
    key = {"total_changes": 40, "items": items,
           "groups": {"flagged": {"population": 30, "sampled": 10}, "unflagged": {"population": 10, "sampled": 5}}}
    gold = {cid: {"wide": cid in {"f0", "f1", "f2", "f3", "u0"}} for cid in items}
    est = pilot.weighted_estimates(key, gold, "wide")
    assert est["flagged"]["estimated_risky"] == pytest.approx(12.0)   # 30 × 4/10
    assert est["unflagged"]["estimated_risky"] == pytest.approx(2.0)  # 10 × 1/5
    assert est["high_risk_rate"] == pytest.approx(14 / 40)
    assert est["flag_recall"] == pytest.approx(12 / 14)
    assert est["flag_precision"] == pytest.approx(0.4)


def test_jaccard():
    assert pilot.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert pilot.jaccard(set(), set()) == 1.0




def test_gold_is_derived_from_labels_not_the_needs_review_column():
    """#16：参考答案由五分类推出；另填的 needs_review 只作对照，不进参考答案。"""
    a = _sheet({"c1": ("stronger_claim", "0"), "c2": ("no_risk", "1"), "c3": ("no_risk", "")})
    b = _sheet({"c1": ("no_risk", "0"), "c2": ("no_risk", "1"), "c3": ("ai_slop", "")})
    gold = pilot.gold_labels(a, b)
    assert gold["c1"] == {"wide": True, "strict": False}
    assert gold["c2"] == {"wide": False, "strict": False}                 # 两人都填了需要看，但标签都是 no_risk
    assert gold["c3"] == {"wide": True, "strict": False}
    rows = {r["类别"] for r in pilot.agreement_table(a, b)[0]}
    assert "是否需要人看（直接填，仅对照）" not in rows                   # 有人没填这一列时不报这一行
