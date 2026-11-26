"""audit() 与 validate_report()（A1、A3–A7 及模型缺失时的退回）。"""

import copy

import pytest

from acceptance_desk import audit, validate_report
from acceptance_desk.text import normalize

EMPTY_SUMMARY = {"total_changes": 0, "flagged": 0, "unflagged": 0, "needs_review": 0, "auto_passed": 0,
                 "by_alignment": {"1-1": 0, "1-2": 0, "2-1": 0, "0-1": 0, "1-0": 0},
                 "by_reason": {}, "by_label": None}


def check(o, r, **kw):
    rep = audit(o, r, **kw)
    assert validate_report(rep, normalize(o), normalize(r)) == []
    return rep


def test_a1_schema_example_rules_only(schema_example):
    inp, exp = schema_example
    rep = check(inp["original"], inp["revised"], mode="rules_only")
    assert rep["run"]["mode"] == "rules_only" and rep["run"]["models"] is None
    assert rep["document"] == exp["document"]
    assert len(rep["changes"]) == len(exp["changes"])
    for got, want in zip(rep["changes"], exp["changes"]):
        for k in ("id", "alignment", "original", "revised", "classification", "decision"):
            assert got[k] == want[k], (got["id"], k)
        assert got["features"]["rules"] == want["features"]["rules"], got["id"]
        assert got["features"]["nli"] is None and got["features"]["similarity"] is None
        assert got["review"]["reasons"] == [r for r in want["review"]["reasons"] if r["source"] != "nli"]
        assert got["review"]["needs_review"] is True and got["review"]["basis"] == "untrained"
    by_id = {c["id"]: c for c in rep["changes"]}
    assert [r["code"] for r in by_id["c004"]["review"]["reasons"]] == ["number_added"]
    assert sum(c["review"]["flagged"] for c in rep["changes"]) == 6
    assert by_id["c005"]["review"]["flagged"] is True
    s = rep["summary"]
    assert s["auto_passed"] == 0 and s["needs_review"] == 6 and s["flagged"] == 6
    assert s["by_reason"] == {k: v for k, v in exp["summary"]["by_reason"].items() if not k.startswith("nli")}
    assert {k: v for k, v in rep["run"].items() if k not in ("created_at", "mode", "models")} == \
        {k: v for k, v in exp["run"].items() if k not in ("created_at", "mode", "models")}


def test_a3_empty():
    rep = check("", "", mode="rules_only")
    assert rep["changes"] == [] and rep["summary"] == EMPTY_SUMMARY
    assert rep["document"]["original_chars"] == 0 and rep["document"]["original_paragraphs"] == 0


def test_a4_a5_report_level():
    rep = check("", "甲。乙。", mode="rules_only")
    assert [c["original"] for c in rep["changes"]] == [{"text": "", "start": 0, "end": 0, "paragraph": None}] * 2
    assert [(c["revised"]["start"], c["revised"]["end"]) for c in rep["changes"]] == [(0, 2), (2, 4)]
    assert rep["summary"]["by_reason"] == {"sentence_inserted": 2}
    rep = check("甲。乙。", "", mode="rules_only")
    assert [c["revised"] for c in rep["changes"]] == [{"text": "", "start": 0, "end": 0, "paragraph": None}] * 2


def test_a6_identical():
    t = "本研究采用问卷。\n\n结论可能成立。"
    assert check(t, t, mode="rules_only")["summary"] == EMPTY_SUMMARY


def test_a7_crlf():
    rep = check("问卷可能有效。\r\n\r\n结论成立。", "问卷有效。\r\n\r\n结论成立。", mode="rules_only")
    (c,) = rep["changes"]
    assert c["alignment"] == "1-1"
    assert (c["original"]["start"], c["original"]["end"]) == (0, 7)
    assert c["features"]["rules"]["hedge_removed"] == ["可能"]


def test_deterministic_except_time(schema_example):
    inp, _ = schema_example
    a = audit(inp["original"], inp["revised"], mode="rules_only")
    b = audit(inp["original"], inp["revised"], mode="rules_only")
    a["run"].pop("created_at"), b["run"].pop("created_at")
    assert a == b


def test_auto_falls_back_when_models_missing(tmp_path):
    with pytest.warns(RuntimeWarning, match="rules_only"):
        rep = audit("问卷可能有效。", "问卷有效。", model_cache=str(tmp_path / "nope"))
    assert rep["run"]["mode"] == "rules_only" and rep["run"]["models"] is None
    assert validate_report(rep, "问卷可能有效。", "问卷有效。") == []


def test_full_raises_when_models_missing(tmp_path):
    with pytest.raises(RuntimeError, match="模型不可用"):
        audit("甲。", "乙。", mode="full", model_cache=str(tmp_path / "nope"))


def test_bad_mode():
    with pytest.raises(ValueError):
        audit("甲。", "乙。", mode="fast")


def test_validate_report_catches_errors(schema_example):
    inp, _ = schema_example
    o, r = normalize(inp["original"]), normalize(inp["revised"])
    good = audit(o, r, mode="rules_only")
    bad = copy.deepcopy(good)
    bad["changes"][0]["original"]["start"] += 1
    bad["changes"][1]["review"]["flagged"] = False
    bad["changes"][2]["features"]["rules"]["slop_added"] = []
    bad["summary"]["flagged"] = 99
    bad["changes"][3]["decision"]["status"] = "auto_passed"
    errs = validate_report(bad, o, r)
    joined = "\n".join(errs)
    for frag in ("c001: original 区间", "c002: flagged", "c003: slop_added", "summary", "c004: 未训练时"):
        assert frag in joined, frag


def test_lexicon_path_override(tmp_path):
    from acceptance_desk.lexicon import default_lexicon_path
    text = default_lexicon_path().read_text(encoding="utf-8").replace('version = "0.2"', 'version = "test"')
    p = tmp_path / "lex.toml"
    p.write_text(text, encoding="utf-8")
    rep = audit("甲。", "乙。", mode="rules_only", lexicon_path=str(p))
    assert rep["run"]["lexicon_version"] == "test"


# ---- 回归：位置与概率校验（PR #14 审阅 2）

def _base():
    return audit("甲。", "甲！", mode="rules_only")


@pytest.mark.parametrize("patch,frag", [
    ({"start": -30, "end": 2}, "越界"),
    ({"start": 0, "end": 3}, "越界"),
    ({"start": 1, "end": 0}, "越界"),
    ({"start": False, "end": 2}, "必须是整数"),
    ({"start": 0.0, "end": 2}, "必须是整数"),
])
def test_validate_rejects_bad_side_span(patch, frag):
    r = _base()
    r["changes"][0]["original"].update(patch)
    errs = validate_report(r, "甲。", "甲！")
    assert any("c001: original" in e and frag in e for e in errs), errs


def test_validate_rejects_bad_hit_span():
    o, r_ = "问卷可能有效。", "问卷有效。"
    r = audit(o, r_, mode="rules_only")
    r["changes"][0]["features"]["rules"]["hits"][0].update(start=-5, end=4)
    errs = validate_report(r, o, r_)
    assert any("命中 可能" in e and "越界" in e for e in errs), errs


def _with_nli(fwd=None, bwd=None, sim=0.9):
    r = _base()
    ok = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}
    r["changes"][0]["features"]["nli"] = {"forward": fwd or dict(ok), "backward": bwd or dict(ok)}
    r["changes"][0]["features"]["similarity"] = sim
    r["run"]["mode"] = "full"
    r["run"]["models"] = {}
    return r


def test_validate_accepts_valid_probabilities():
    assert validate_report(_with_nli(), "甲。", "甲！") == []


@pytest.mark.parametrize("fwd,frag", [
    ({"entailment": 1.5, "neutral": -0.5, "contradiction": 0.0}, "nli.forward.entailment"),  # 和为 1 但单项越界
    ({"entailment": float("nan"), "neutral": 0.5, "contradiction": 0.5}, "有限数"),
    ({"entailment": True, "neutral": 0.0, "contradiction": 0.0}, "有限数"),
    ({"entail": 0.9, "neutral": 0.05, "contradiction": 0.05}, "键应为"),
    ({"entailment": 0.5, "neutral": 0.2, "contradiction": 0.2}, "概率和不为 1"),
])
def test_validate_rejects_bad_probabilities(fwd, frag):
    errs = validate_report(_with_nli(fwd=fwd), "甲。", "甲！")
    assert any(frag in e for e in errs), errs


@pytest.mark.parametrize("sim", [1.5, float("inf"), True])
def test_validate_rejects_bad_similarity(sim):
    errs = validate_report(_with_nli(sim=sim), "甲。", "甲！")
    assert any("similarity" in e for e in errs), errs
