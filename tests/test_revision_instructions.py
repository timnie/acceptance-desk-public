"""作者要求自动发送、覆盖旧决定、连续对话和失败处理的行为回归。"""

import asyncio
import json
from dataclasses import replace

import pytest

from acceptance_desk import audit
from acceptance_desk.feedback import feedback_markdown
from review_ui import state
from workbench.next_round import DEFAULT_REVISION_OPTIONS, RevisionRequest, generate_revision
from workbench.session import Session

ORIGINAL = "发出三份问卷。样本来自访谈。"
REVISED = "发出五份问卷。样本来自调查。"
CHANGED = "本次演练发出三份问卷。样本来自访谈。"


def prepare(status="pending", *, instructions=None):
    session = Session()
    session.load(ORIGINAL, REVISED, audit(ORIGINAL, REVISED, mode="rules_only"),
                 title="虚构稿", names={}, formats={})
    if status != "pending":
        session.decide("c001", status, "先前指定的句子。" if status == "edited" else None)
    state.set_note(session.review, "c001", "在第一句中说明问卷只用于演练。")
    options = dict(DEFAULT_REVISION_OPTIONS)
    if instructions:
        options["instructions"] = instructions
    return session, RevisionRequest.prepare(session, options)


def response(request, text=CHANGED, status="done"):
    return json.dumps({"document": text, "executions": [
        {"id": task.change_id, "status": status,
         "detail": "第一句已注明本次演练。" if status == "done" else "请提供文献全文和页码。"}
        for task in request.tasks
    ]}, ensure_ascii=False)


@pytest.mark.parametrize("status", ["pending", "accepted", "rejected", "edited"])
def test_every_note_is_sent_and_can_override_prior_choice(status):
    session, request = prepare(status)
    assert request.tasks[0].text in request.feedback
    assert not request.confirmed_edits
    assert not request.reply_issue(request.parse_reply(response(request)))
    assert session.by_id["c001"]["decision"]["status"] == status
    assert request.matches(session)


def test_normal_feedback_export_keeps_its_existing_selection_rules():
    session, request = prepare()
    exported = feedback_markdown(session.review, ORIGINAL, REVISED, title="虚构稿", scope="local")
    assert request.tasks[0].text not in exported
    assert request.tasks[0].text in request.feedback


def test_revision_request_treats_an_unnoted_decision_as_context():
    session, _ = prepare()
    session.decide("c002", "rejected")
    request = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    instructions, content = request.prompt.split("\n\n", 1)
    payload = json.loads(content)
    assert [task["id"] for task in payload["必须逐条执行的作者要求"]] == ["c001"]
    assert "只为「必须逐条执行的作者要求」列表中的 id 返回执行说明" in instructions
    assert "c002 · 保留原稿" in request.feedback
    assert "请恢复为以下原稿内容" not in request.feedback
    exported = feedback_markdown(session.review, ORIGINAL, REVISED, title="虚构稿", scope="local")
    assert "请恢复为以下原稿内容" in exported


def test_followup_keeps_prior_candidate_and_all_nonconflicting_requirements():
    _, request = prepare()
    reply = request.parse_reply(response(request))
    request = replace(request, execution_log=reply.executions)
    followup = request.follow_up("第二句改成：样本来自模拟访谈。", CHANGED)
    payload = json.loads(followup.prompt.split("\n\n", 1)[1])
    assert payload["对话记录"][0]["role"] == "assistant"
    assert json.loads(payload["对话记录"][0]["content"]) == json.loads(response(request))
    assert payload["对话记录"][1] == {"role": "user", "content": "第二句改成：样本来自模拟访谈。"}
    assert {task.change_id for task in followup.tasks} == {"c001", "followup-1"}
    assert len(request.conversation) == 0
    assert followup.reply_issue(followup.parse_reply(response(followup)))
    updated = CHANGED.replace("访谈", "模拟访谈")
    assert not followup.reply_issue(followup.parse_reply(response(followup, updated)))


def test_custom_instructions_allow_layout_changes_and_protect_unnoted_decisions():
    session, request = prepare("rejected", instructions="每句分段，保留所有数字。")
    session.decide("c002", "accepted")
    request = RevisionRequest.prepare(session, {**DEFAULT_REVISION_OPTIONS, "instructions": request.instructions})
    candidate = "本次演练发出三份问卷。\n\n样本来自访谈。"
    assert request.preserve_local_layout(candidate) == candidate
    assert {task.change_id for task in request.tasks} == {"c001", "general"}
    assert "c002" in request.candidate_issue(candidate)
    assert not request.candidate_issue(candidate.replace("访谈", "调查"))


@pytest.mark.parametrize("followup", [False, True])
def test_a_local_note_protects_the_other_decision_even_after_followup(followup):
    session, _ = prepare("rejected")
    session.decide("c002", "rejected")
    request = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    if followup:
        request = request.follow_up("在第一句写明本次是演练。", ORIGINAL)
    assert not request.reply_issue(request.parse_reply(response(request)))
    changed_elsewhere = CHANGED.replace("访谈", "调查")
    assert "c002" in request.reply_issue(request.parse_reply(response(request, changed_elsewhere)))


@pytest.mark.parametrize("separator", ["", "\n\n"])
@pytest.mark.parametrize("tail", ["", "此前已有明确说明。"])
def test_a_duplicate_elsewhere_does_not_hide_a_changed_decision(separator, tail):
    sentence = "访谈中有十二人选择甲方案。"
    suffix = separator + tail if tail else ""
    original = sentence + separator + sentence + suffix
    revised = sentence.replace("十二", "二十") + separator + sentence.replace("十二", "三十") + suffix
    session = Session()
    session.load(original, revised, audit(original, revised, mode="rules_only"),
                 title="虚构稿", names={}, formats={})
    session.decide("c001", "rejected")
    request = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    assert request.baseline == original
    candidate = original.replace("十二人", "二十人", 1).replace("明确说明", "另一种说明")
    assert "c001" in request.candidate_issue(candidate)
    assert not request.candidate_issue("新增引言。\n\n" + original)


def test_each_conflict_requires_separate_author_confirmation():
    session, _ = prepare()
    for cid in session.order:
        session.decide(cid, "rejected")
        state.set_note(session.review, cid, "")
    request = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    reply = request.parse_reply(response(request, REVISED))
    check = request.check_reply(reply)
    assert [(c.change_id, c.confirmed, c.candidate) for c in check.conflicts] == [
        ("c001", "发出三份问卷。", "发出五份问卷。"),
        ("c002", "样本来自访谈。", "样本来自调查。"),
    ]
    assert "c002" in check.blocking_reason({"c001"})
    assert not check.blocking_reason({"c001", "c002"})
    blocked = request.check_reply(request.parse_reply(response(request, "```text\n" + REVISED + "\n```")))
    assert blocked.blocking_reason({"c001", "c002"})


@pytest.mark.parametrize("bad", ["原文", "[]", "null", '{"document":"稿","executions":[]}',
                                '{"document":"稿","executions":[{}]}'])
def test_missing_execution_reports_cannot_be_treated_as_success(bad):
    _, request = prepare()
    with pytest.raises(ValueError, match="逐条执行说明"):
        request.parse_reply(bad)


def test_duplicate_unknown_and_empty_reports_are_rejected():
    _, request = prepare()
    data = json.loads(response(request))
    entry = data["executions"][0]
    for entries in ([entry, entry], [{**entry, "id": "other"}], [{**entry, "detail": " "}]):
        with pytest.raises(ValueError):
            request.parse_reply(json.dumps({**data, "executions": entries}))


def test_blocked_request_explains_missing_material_and_cannot_advance():
    _, request = prepare()
    reply = request.parse_reply(response(request, ORIGINAL, "blocked"))
    assert "未执行" in request.reply_issue(reply)
    assert "页码" in reply.executions[0].detail


@pytest.mark.parametrize("first", ["原文", "unchanged"])
def test_incomplete_or_unchanged_result_is_retried_once(first):
    _, request = prepare()
    calls, reasons = [], []

    class Connection:
        async def generate(self, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return response(request, ORIGINAL) if first == "unchanged" else first
            return response(request)

    reply = asyncio.run(generate_revision(Connection(), request, reasons.append))
    assert len(calls) == 2 and len(reasons) == 1
    assert reply.text == CHANGED
    assert "上次回复未通过检查" in calls[1]


def test_repeated_noop_is_visible_and_blocked_response_is_not_retried():
    _, request = prepare()
    for status, expected_calls in [("done", 2), ("blocked", 1)]:
        calls = []

        class Connection:
            async def generate(self, prompt):
                calls.append(prompt)
                return response(request, ORIGINAL, status)

        reply = asyncio.run(generate_revision(Connection(), request))
        assert len(calls) == expected_calls
        assert request.reply_issue(reply)


def test_failed_second_response_and_cancellation_propagate():
    _, request = prepare()

    class Invalid:
        async def generate(self, prompt):
            return "原文"

    with pytest.raises(ValueError):
        asyncio.run(generate_revision(Invalid(), request))

    class Cancelled:
        async def generate(self, prompt):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(generate_revision(Cancelled(), request))


def test_without_new_requirements_existing_decisions_are_still_protected():
    session, _ = prepare("rejected")
    state.set_note(session.review, "c001", "")
    request = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    assert not request.tasks
    reply = request.parse_reply(json.dumps({"document": REVISED, "executions": []}))
    assert "c001" in request.reply_issue(reply)
    assert request.confirmed_edits


def test_selected_reference_reaches_model_and_followup_without_becoming_an_author_task():
    session, baseline = prepare(instructions="只处理作者指定的位置。")
    request = RevisionRequest.prepare(session, {
        **DEFAULT_REVISION_OPTIONS,
        "instructions": baseline.instructions,
        "reference_ids": ("evidence",),
    })
    prompts = []

    class Connection:
        async def generate(self, prompt):
            prompts.append(prompt)
            return response(request)

    assert asyncio.run(generate_revision(Connection(), request)).text == CHANGED
    directions, content = prompts[0].split("\n\n", 1)
    payload = json.loads(content)
    assert set(payload["学术修订参考"]) == {"论证与证据"}
    assert "中间推理" in payload["学术修订参考"]["论证与证据"]
    assert "只使用参考处理作者授权的范围" in directions
    assert [task["id"] for task in payload["必须逐条执行的作者要求"]] == ["c001", "general"]
    assert "学术修订参考" not in json.loads(baseline.prompt.split("\n\n", 1)[1])
    followup = request.follow_up("保留专业术语。", CHANGED)
    assert json.loads(followup.prompt.split("\n\n", 1)[1])["学术修订参考"] == payload["学术修订参考"]
