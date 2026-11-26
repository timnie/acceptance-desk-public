"""工作台跨页面恢复、批量撤销和不落盘开关；不依赖界面框架。"""

import copy
import json

import pytest

from acceptance_desk import audit, document_segments, merge_decisions
from acceptance_desk.text import normalize
from review_ui import state
from workbench.session import MODEL_FILTER, STORE_KEY, Session
from workbench import view


@pytest.fixture
def session():
    s = Session({})
    original = "研究可能有效。\n\n问卷共18份。\n\n结论尚需验证。"
    revised = "研究有效。\n\n问卷共20份。\n\n结论已经验证。"
    report = audit(original, revised, mode="rules_only")
    s.load(original, revised, report, title="虚构示例", names={}, formats={"original": "txt", "revised": "txt"})
    assert len(s.order) == 3
    return s


def test_restore_old_page_decisions_and_notes(session):
    state.set_decision(session.review, "c001", "rejected")
    state.set_note(session.review, "c001", "请保留限定")
    state.set_decision(session.review, "c002", "edited", "问卷共十九份。")
    saved = state.export_review(session.review).encode("utf-8-sig")
    session.batch(session.order, "accepted")
    assert session.undo
    message = session.restore(saved)
    assert "2 / 3" in message and not session.undo
    assert session.by_id["c001"]["decision"]["note"] == "请保留限定"
    assert session.by_id["c002"]["decision"]["final_text"] == "问卷共十九份。"
    assert session.by_id["c003"]["decision"]["status"] == "pending"
    assert "问卷共十九份。" in merge_decisions(session.review, session.original, session.revised, pending="original").text


@pytest.mark.parametrize("problem", ["json", "hash", "missing", "invalid_decision", "auto_passed", "encoding"])
def test_bad_restore_keeps_decisions_undo_and_disk(session, problem):
    session.batch(["c001"], "rejected")
    session.persist()
    before = copy.deepcopy((session.review, session.undo, session.storage, session.cur))
    data = json.loads(state.export_review(session.review))
    if problem == "hash": data["document"]["original_sha256"] = "0" * 64
    if problem == "missing": data["changes"].pop()
    if problem == "invalid_decision": data["changes"][0]["decision"]["final_text"] = "不符"
    if problem == "auto_passed": data["changes"][0]["decision"]["status"] = "auto_passed"
    source = b"\xff" if problem == "encoding" else "{" if problem == "json" else json.dumps(data)
    with pytest.raises(state.ReviewError):
        session.restore(source)
    assert (session.review, session.undo, session.storage, session.cur) == before


def test_batch_only_pending_and_undo_restores_full_decisions(session):
    state.set_decision(session.review, "c001", "edited", "作者的文字。")
    state.set_note(session.review, "c002", "保留说明")
    before = copy.deepcopy(session.review)
    session.cur = "c002"
    assert session.batch(["c001", "c002"], "accepted") == ["c002"]
    assert session.by_id["c001"]["decision"] == before["changes"][0]["decision"]
    assert session.by_id["c003"]["decision"]["status"] == "pending"
    assert session.by_id["c002"]["decision"]["note"] == "保留说明"
    entry = state.undo(session.review, session.undo)
    assert entry["cur"] == "c002" and session.review == before
    assert session.batch([], "accepted") == [] and not session.undo


def test_storage_is_detached_and_can_be_disabled(session):
    session.persist()
    saved = copy.deepcopy(session.storage[STORE_KEY])
    session.save_enabled = False
    session.batch(["c001"], "accepted")
    session.persist()
    assert session.storage[STORE_KEY] == saved
    session.forget()
    session.persist()
    assert STORE_KEY not in session.storage
    session.save_enabled = True
    session.persist()
    restored = Session(session.storage)
    restored.load(session.original, session.revised, session.report, title=session.title, names=session.names,
                  formats=session.formats, saved_review=session.storage[STORE_KEY]["review"])
    assert restored.by_id["c001"]["decision"]["status"] == "accepted"


def test_bad_load_does_not_destroy_loaded_document(session):
    session.persist()
    before = copy.deepcopy(vars(session))
    with pytest.raises(state.ReviewError):
        session.load("另一篇。", "另一篇改稿。", audit("另一篇。", "另一篇改稿。", mode="rules_only"),
                     title="另一篇", names={}, formats={}, saved_review=state.export_review(session.review))
    assert vars(session) == before


@pytest.mark.parametrize("original,revised", [
    ("", "新增一句。\n\n再加一句。"),
    ("删除一句。\n\n再删一句。", ""),
    ("同一句。同一句。结论可能成立。", "同一句。同一句。结论成立。"),
    ("本研究采用问卷调查。数据在三所高校收集。", "本研究采用问卷调查，并在三所高校收集数据。"),
    ("  第一段。\n\n第二段。\n", "第一段。\n第二段。\n\n"),
    ("甲。\r\n\r\n乙。", "甲！\r乙。"),
    ("", ""),
    ("  \n\n", "\t\n"),
])
def test_comparison_reconstructs_each_source_exactly(original, revised):
    report = audit(original, revised, mode="rules_only")
    rows = view.comparison_rows(document_segments(report, original, revised), original, revised)
    for side, text in (("original", original), ("revised", revised)):
        assert "".join(r[side + "_gap"] + r[side] for r in rows) == normalize(text)
    assert [r["change_id"] for r in rows if r["change_id"]] == [s["change_id"] for s in
        document_segments(report, original, revised) if s["change_id"]]


def test_comparison_escapes_source_and_keeps_source_after_edit():
    report = audit("<b>研究可能有效。</b>", "<script>研究有效。</script>", mode="rules_only")
    for c in report["changes"]:
        state.set_decision(report, c["id"], "edited", "作者的决定。")
        for side in ("original", "revised"):
            text = view.comparison_html(c, side)
            assert "<script>" not in text and "<b>" not in text and "作者的决定" not in text


def test_redo_restores_full_decisions_and_new_action_clears_it(session):
    before = copy.deepcopy(session.review)
    session.record(["c001"], "保留原稿")
    state.set_decision(session.review, "c001", "rejected")
    state.set_note(session.review, "c001", "保留限定")
    decided = copy.deepcopy(session.review)
    assert session.travel("undo")
    assert session.review == before
    assert session.travel("redo")
    assert session.review == decided
    session.travel("undo")
    session.record(["c002"], "编辑")
    state.set_decision(session.review, "c002", "edited", "作者改写。")
    assert not session.redo
    assert session.travel("redo") is None


def test_batch_redo_is_atomic_and_preserves_existing_edits(session):
    state.set_decision(session.review, "c001", "edited", "作者的文字。")
    session.batch(session.order, "accepted")
    decided = copy.deepcopy(session.review)
    session.travel("undo")
    assert session.by_id["c002"]["decision"]["status"] == "pending"
    session.travel("redo")
    assert session.review == decided


def test_workspace_restores_empty_draft_and_location_without_changing_decisions(session):
    session.drafts["c002"] = ""
    session.cur, session.flt, session.display = "c002", "all", "compare"
    session.search = "问卷"
    session.persist()
    saved = copy.deepcopy(session.storage[STORE_KEY])
    restored = Session({})
    restored.load(saved["original"], saved["revised"], session.report, title=saved["title"],
                  names=saved["names"], formats=saved["formats"], saved_review=saved["review"])
    restored.resume_workspace(saved)
    assert restored.drafts == {"c002": ""}
    assert (restored.cur, restored.flt, restored.display, restored.search) == ("c002", "all", "compare", "问卷")
    assert restored.by_id["c002"]["decision"]["status"] == "pending"
    assert not restored.undo and not restored.redo


def test_workspace_ignores_foreign_or_malformed_optional_state(session):
    session.resume_workspace({"original": "另一稿", "revised": "另一改稿", "workspace": {"drafts": {"c001": "不属于此稿"}}})
    assert not session.drafts
    session.resume_workspace({"original": session.original, "revised": session.revised,
                              "workspace": {"drafts": {"c001": "草稿", "c999": "旧稿", "c002": 1},
                                            "current": ["c999"], "filter": "bad", "display": "bad"}})
    assert session.drafts == {"c001": "草稿"}
    assert session.cur in session.by_id and session.flt in state.FILTERS and session.display == "proof"


def test_disabled_storage_keeps_drafts_in_page_only(session):
    session.save_enabled = False
    session.drafts["c001"] = "编辑草稿"
    session.persist()
    assert STORE_KEY not in session.storage
    assert session.drafts["c001"] == "编辑草稿"


def test_failed_storage_does_not_mark_new_revision_saved(session):
    class FailingStorage(dict):
        def __setitem__(self, key, value):
            raise OSError("disk full")
    session.storage = FailingStorage()
    with pytest.raises(OSError):
        session.persist()
    assert session.saved_revision is None


def test_restoring_progress_keeps_local_unsubmitted_drafts(session):
    session.drafts["c001"] = "尚未提交的编辑"
    source = state.export_review(session.review)
    session.restore(source)
    assert session.drafts == {"c001": "尚未提交的编辑"}


def test_loading_another_document_does_not_reuse_draft_ids(session):
    session.drafts["c001"] = "旧稿草稿"
    original, revised = "样本可能足够。", "样本足够。"
    session.load(original, revised, audit(original, revised, mode="rules_only"), title="新稿", names={}, formats={})
    assert not session.drafts


def test_new_review_starts_with_all_changes_and_restores_saved_filter(session):
    assert session.flt == "all"
    assert session.visible() == session.order
    session.flt = "pending"
    session.persist()
    saved = copy.deepcopy(session.storage[STORE_KEY])
    session.load(session.original, session.revised, session.report, title=session.title,
                 names=session.names, formats=session.formats, saved_review=saved["review"])
    session.resume_workspace(saved)
    assert session.flt == "pending"


def test_unpaired_rewrite_opens_in_full_draft_comparison_with_decisions_intact():
    original = "参与者均来自同一所学校。"
    revised = "问卷答案由两位研究者独立编码。"
    report = audit(original, revised, mode="rules_only")
    session = Session()
    session.load(original, revised, report, title="旧标题", names={}, formats={})
    assert session.display == "drafts"

    change_id = session.order[0]
    session.decide(change_id, "accepted")
    session.title = "新标题"
    session.persist()

    restored = Session(session.storage)
    restored.restore_project(session.storage["workbench"], report)
    assert restored.title == "新标题"
    assert restored.display == "drafts"
    assert restored.by_id[change_id]["decision"]["status"] == "accepted"


def test_reference_filter_works_without_run03_and_restores():
    original = "某条前提另见第一批判（KrV A301/B357）。\n同一出处支持这个结论（KpV 5:4）。"
    revised = "同一出处支持这个结论（KpV 5：4）。\n新版转而引用另一段（KrV A558/B586）。"
    report = audit(original, revised, mode="rules_only")
    session = Session()
    session.load(original, revised, report, title="虚构示例", names={}, formats={"original": "txt", "revised": "txt"})
    assert session.model_suggestions is None
    selected = [session.by_id[cid] for cid in session.visible(MODEL_FILTER)]
    assert any("A301/B357" in change["original"]["text"] for change in selected)
    assert any("A558/B586" in change["revised"]["text"] for change in selected)
    assert all("5:4" not in change["original"]["text"] and "5：4" not in change["revised"]["text"]
               for change in selected)
    session.flt = MODEL_FILTER
    session.persist()
    restored = Session()
    restored.restore_project(session.storage[STORE_KEY], report)
    assert restored.flt == MODEL_FILTER
    assert restored.visible() == session.visible()


def test_reference_evidence_is_visible_when_the_only_change_is_a_citation():
    original = "这条前提见第一批判（KrV A301/B357）。"
    revised = "这条前提见第一批判（KrV A558/B586）。"
    report = audit(original, revised, mode="rules_only")
    session = Session()
    session.load(original, revised, report, title="虚构示例", names={}, formats={"original": "txt", "revised": "txt"})

    assert len(session.order) == 1
    assert session.visible(MODEL_FILTER) == session.order
