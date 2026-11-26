"""作者意见来自实际决定，不把提示或未决定项变成作者许可。"""

import copy
import json

import pytest

from acceptance_desk import (ReviewDataError, audit, build_feedback, change_evidence,
                             feedback_markdown, feedback_text)
from acceptance_desk.feedback_cli import main

NOW = "2026-11-11T22:30:00+08:00"


def decide(change, status, text=None, note=""):
    target = text if status == "edited" else change["original" if status == "rejected" else "revised"]["text"]
    change["decision"] = {"status": status, "final_text": target, "decided_by": "user", "decided_at": NOW, "note": note}


@pytest.fixture
def source():
    original = "研究可能支持这一判断。\n\n访谈对象共18人。\n\n学校开放时间为九点。\n\n值得注意的是，结果尚需验证。"
    revised = "研究支持这一判断。\n\n访谈对象共20人。\n\n学校开放时间为9点。\n\n值得注意的是，结果已经验证。"
    report = audit(original, revised, mode="rules_only")
    assert len(report["changes"]) == 4
    decide(report["changes"][0], "rejected", note="原文只说可能。\n请保留限定。")
    decide(report["changes"][1], "edited", "访谈对象共18人。", note="按我的文本。")
    decide(report["changes"][2], "accepted", note="已核对。")
    return report, original, revised


def build(source, **options):
    return build_feedback(*source, **{"title": "虚构材料", "scope": "local", "now": NOW, **options})


def test_author_actions_and_accepted_preview_default_pending_excluded(source):
    before = copy.deepcopy(source[0])
    result = build(source)
    assert result["counts"] == {"actions": 2, "accepted": 1, "pending": 0, "exported": 3, "total_changes": 4}
    sections = result["sections"]
    assert [e["change_id"] for e in sections["actions"]] == ["c001", "c002"]
    assert [e["target_text"] for e in sections["actions"]] == ["研究可能支持这一判断。", "访谈对象共18人。"]
    assert sections["accepted"][0]["change_id"] == "c003"
    assert "excerpts" not in sections["accepted"][0]
    assert sections["actions"][0]["note"] == "原文只说可能。\n请保留限定。"
    assert sections["actions"][1]["position"]["original"]["paragraph"] == 2
    assert source[0] == before


def test_explicit_scope_and_selected_ids_do_not_change_decisions(source):
    assert "尽量保持不变" in build(source)["scope_instruction"]
    rewritten = build(source, scope="rewrite", selected_ids=["c003", "c002"])
    assert "重新改写" in rewritten["scope_instruction"]
    assert rewritten["sections"]["actions"][0]["change_id"] == "c002"
    assert "参考" in rewritten["sections"]["accepted"][0]["request"]
    assert build(source, selected_ids=[])["counts"]["exported"] == 0
    with pytest.raises(ReviewDataError, match="待定"):
        build(source, selected_ids=["c004"])
    pending = build(source, selected_ids=["c004"], include_pending=True)
    assert pending["sections"]["pending"][0]["status"] == "pending"
    assert "target_text" not in pending["sections"]["pending"][0]


@pytest.mark.parametrize("options", [{"scope": "unknown"}, {"scope": []}, {"title": " "}, {"include_pending": 1},
    {"include_context": "false"}, {"selected_ids": "c001"}, {"selected_ids": ["c001", "c001"]},
    {"selected_ids": ["absent"]}, {"selected_ids": [{}]}, {"now": "not-time"}, {"now": "2026-11-11"}, {"now": 1}])
def test_invalid_export_options_are_domain_errors(source, options):
    with pytest.raises(ReviewDataError):
        build(source, **options)


def test_evidence_keeps_facts_rules_and_model_uncertainty_separate(source):
    change = copy.deepcopy(source[0]["changes"][0])
    change["review"]["reasons"].append({"source": "nli", "code": "nli_not_entailed", "detail": "0.999 不该当事实"})
    evidence = change_evidence(change)
    assert "删去“可能”" in evidence["observed"]
    assert evidence["rules"] == ["删去限定词“可能”"]
    assert "可能有误" in evidence["model"][0] and "0.999" not in evidence["model"][0]
    change["review"]["reasons"] = [None]
    with pytest.raises(ReviewDataError):
        change_evidence(change)


def test_same_export_is_deterministic_and_markdown_payload_is_fenced(source):
    report, original, revised = source
    note = '<script>alert(1)</script>\n```\n# 假标题\n``````\n| 表格 |'
    report["changes"][0]["decision"]["note"] = note
    options = dict(title="标题\n# 另一标题", scope="local", now=NOW)
    first = feedback_markdown(report, original, revised, **options)
    assert first == feedback_markdown(report, original, revised, **options)
    assert note in first and "```````text\n" + note + "\n```````" in first
    assert "```text\n标题\n# 另一标题\n```" in first
    assert first.index("需要按作者意见处理") < first.index("作者已采用\n")
    plain = feedback_text(report, original, revised, **options)
    assert "    <script>alert(1)</script>" in plain
    assert not plain.startswith("#")


def test_context_stays_inside_same_paragraph_and_can_be_disabled():
    original = "上一段。\n\n本段前句。结论可能成立。"
    revised = "上一段。\n\n本段前句。结论成立。"
    report = audit(original, revised, mode="rules_only")
    decide(report["changes"][0], "rejected")
    result = build((report, original, revised))
    action = result["sections"]["actions"][0]
    assert action["context"]["original"]["text"] == "本段前句。"
    assert action["position"]["original"]["sentence"] == 2
    assert build((report, original, revised), include_context=False)["sections"]["actions"][0]["context"] == {}


def test_long_quotes_cover_every_change_but_keep_full_author_target():
    original = "甲" * 100 + "原" + "乙" * 100 + "旧" + "丙" * 100 + "。"
    revised = "甲" * 100 + "新" + "乙" * 100 + "改" + "丙" * 100 + "。"
    report = audit(original, revised, mode="rules_only")
    target = "丁" * 600 + "。"
    decide(report["changes"][0], "edited", target)
    action = build((report, original, revised))["sections"]["actions"][0]
    assert action["target_text"] == target
    assert len(action["excerpts"]) == 2
    assert all(e["original"]["excerpt"] for e in action["excerpts"])
    assert "新" in action["excerpts"][0]["revised"]["text"]
    assert "改" in action["excerpts"][1]["revised"]["text"]
    assert target in feedback_markdown(report, original, revised, title="长例子", scope="local", now=NOW)


def test_empty_original_target_means_delete_and_not_a_literal_placeholder():
    original, revised = "原句。", "原句。新增句子。"
    report = audit(original, revised, mode="rules_only")
    decide(report["changes"][0], "rejected")
    action = build((report, original, revised))["sections"]["actions"][0]
    assert action["target_text"] == ""
    text = feedback_markdown(report, original, revised, title="新增", scope="local", now=NOW)
    assert "指定文本为空：请删除这一组，不插入占位文字" in text


@pytest.mark.parametrize("mutation", ["missing_change", "wrong_hash", "wrong_id", "bad_decision", "schema", "version"])
def test_source_validation_before_feedback(source, mutation):
    report, original, revised = source
    if mutation == "missing_change": report["changes"].pop()
    if mutation == "wrong_hash": report["document"]["original_sha256"] = "0" * 64
    if mutation == "wrong_id": report["changes"][0]["id"] = "c099"
    if mutation == "bad_decision": report["changes"][0]["decision"]["final_text"] = "未经选择的内容"
    if mutation == "schema": report["schema_version"] = "other"
    if mutation == "version": report["run"].pop("tool_version")
    with pytest.raises(ReviewDataError):
        build((report, original, revised))


def test_cli_all_formats_and_no_overwrite(source, tmp_path, capsys):
    report, original, revised = source
    record, op, rp = [tmp_path / name for name in ("report.json", "original.txt", "revised.txt")]
    record.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8-sig")
    op.write_text(original, encoding="utf-8-sig"); rp.write_text(revised, encoding="utf-8-sig")
    args = [str(record), str(op), str(rp)]
    for fmt in ("markdown", "txt", "json"):
        out = tmp_path / ("feedback." + fmt)
        main(["feedback", *args, "--title", "虚构", "--scope", "local", "--format", fmt, "--now", NOW, "--out", str(out)])
        assert out.exists()
        if fmt == "json": assert json.loads(out.read_text())["counts"]["exported"] == 3
        before = out.read_bytes()
        with pytest.raises(SystemExit):
            main(["feedback", *args, "--title", "虚构", "--scope", "local", "--out", str(out)])
        assert out.read_bytes() == before
    before = record.read_bytes()
    with pytest.raises(SystemExit):
        main(["hints", *args, "--out", str(record)])
    assert record.read_bytes() == before
    hints_path = tmp_path / "hints.json"
    main(["hints", *args, "--out", str(hints_path)])
    assert json.loads(hints_path.read_text())["hints"][0]["text"] == "值得注意的是"
    bad = tmp_path / "not-written.md"
    with pytest.raises(SystemExit):
        main(["feedback", *args, "--title", "虚构", "--scope", "local", "--select", "absent", "--out", str(bad)])
    assert not bad.exists() and not capsys.readouterr().out


def test_system_auto_pass_is_never_author_approval(source):
    report = source[0]
    report["run"]["classifier"]["status"] = "trained"
    change = report["changes"][2]
    change["review"]["needs_review"] = False
    change["decision"] = {"status": "auto_passed", "final_text": change["revised"]["text"],
                          "decided_by": "system", "decided_at": report["run"]["created_at"], "note": ""}
    assert build(source)["counts"]["accepted"] == 0
    with pytest.raises(ReviewDataError, match="系统自动通过"):
        build(source, selected_ids=["c003"])


def test_core_evidence_agrees_with_existing_display_for_public_examples():
    from pathlib import Path
    from review_ui.render import evidence

    root = Path(__file__).resolve().parents[1]
    for name in ("", "long_"):
        original = (root / "examples" / f"{name}original.txt").read_text()
        revised = (root / "examples" / f"{name}revised.txt").read_text()
        report = audit(original, revised, mode="rules_only")
        for change in report["changes"]:
            assert change_evidence(change) == evidence(change)


def test_cli_requires_explicit_scope_and_new_source_validation(source, tmp_path):
    record, original, revised = [tmp_path / name for name in ("r.json", "o.txt", "r.txt")]
    record.write_text(json.dumps(source[0])); original.write_text(source[1]); revised.write_text(source[2])
    out = tmp_path / "out.md"
    args = [str(record), str(original), str(revised), "--title", "材料", "--out", str(out)]
    with pytest.raises(SystemExit):
        main(["feedback", *args])
    assert not out.exists()
    original.write_text("已换了一份原稿。")
    with pytest.raises(SystemExit):
        main(["feedback", *args, "--scope", "local"])
    assert not out.exists()


def test_reordered_entries_cannot_reassign_ids_and_change_feedback_order(source):
    report = source[0]
    report["changes"].reverse()
    for index, change in enumerate(report["changes"], 1):
        change["id"] = f"c{index:03d}"
    with pytest.raises(ReviewDataError, match="正文顺序"):
        build(source)
