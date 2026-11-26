from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from acceptance_desk import audit
from acceptance_desk.claim_history import relink_evidence
from acceptance_desk.scope_review import ReviewIntent, ScopeReviewError, prepare_scope_review
from experiments.evaluate_scope_review import prepare_cases, score_run

ORIGINAL = "结果来自东区的两次调查。\n\n本结论只适用于东区。"
REVISED = "结果来自东区的两次调查。\n\n本结论适用于全市。"
INTENTS = (ReviewIntent("i1", "保留调查范围。"),)
CASES = Path(__file__).resolve().parents[1] / "examples" / "scope_review"


def prepare(original=ORIGINAL, revised=REVISED, **kwargs):
    report = audit(original, revised, mode="rules_only")
    return prepare_scope_review(report, original, revised, **kwargs)


def finding(**overrides):
    return {
        "unit_id": "u001", "kind": "widened", "support": "insufficient", "intent_ids": ["i1"],
        "evidence": [
            {"paragraph": "o2", "quote": "本结论只适用于东区。", "role": "claim"},
            {"paragraph": "r2", "quote": "本结论适用于全市。", "role": "claim"},
            {"paragraph": "r1", "quote": "结果来自东区的两次调查。", "role": "support"},
        ],
        "effect": "结论扩大到了全市。", "question": "要维持东区范围，还是能补充全市的数据？",
        "missing": "东区以外的调查。", **overrides,
    }


def reply(request, findings, **overrides):
    return json.dumps({"request_id": request.request_id, "skill_version": request.skill_version,
                       "findings": findings, **overrides}, ensure_ascii=False)


def test_validated_review_leaves_decisions_untouched():
    report = audit(ORIGINAL, REVISED, mode="rules_only")
    before = json.dumps(report, ensure_ascii=False)
    request = prepare_scope_review(report, ORIGINAL, REVISED, INTENTS)
    raw = reply(request, [finding()])
    result = request.parse_reply("```json\n" + raw + "\n```")
    assert not result.issues and not result.unchecked_unit_ids
    entry, = result.findings
    assert entry.change_ids == ("c001",)
    assert entry.kind == "widened"
    assert entry.support == "insufficient"
    assert entry.intent_ids == ("i1",)
    for anchor in entry.evidence:
        source = ORIGINAL if anchor.side == "original" else REVISED
        assert source[anchor.start:anchor.end] == anchor.quote
    assert json.dumps(report, ensure_ascii=False) == before


@pytest.mark.parametrize(("patch", "message"), [
    ({"evidence": [{"paragraph": "o2", "quote": "不存在的正文。", "role": "claim"}]}, "引文不在"),
    ({"evidence": [{"paragraph": "o2", "quote": "本结论只适用于东区。", "role": "claim"}]}, "两侧"),
    ({"evidence": [{"paragraph": "r2", "quote": "本结论适用于全市。", "role": "claim"}],
      "support": "sufficient", "missing": ""}, "支撑材料"),
    ({"intent_ids": ["model-guessed"]}, "作者要求不存在"),
    ({"kind": ["widened"]}, "范围判断"),
    ({"missing": ""}, "缺少哪项材料"),
])
def test_invalid_model_item_leaves_unit_for_manual_check(patch, message):
    request = prepare(intents=INTENTS)
    result = request.parse_reply(reply(request, [finding(**patch)]))
    assert not result.findings
    issue, = result.issues
    assert issue.unit_id == "u001" and issue.code == "invalid_finding"
    assert message in issue.detail


def test_repeat_phrase_in_one_paragraph_requires_a_more_specific_quote():
    revised = REVISED + "本结论适用于全市。"
    request = prepare(revised=revised, intents=INTENTS)
    result = request.parse_reply(reply(request, [finding()]))
    assert not result.findings
    assert "重复" in result.issues[0].detail


def test_duplicate_missing_and_unknown_units_are_not_counted_as_checked():
    request = prepare(intents=INTENTS)
    missing = request.parse_reply(reply(request, []))
    assert [issue.code for issue in missing.issues] == ["missing_unit"]
    repeated = request.parse_reply(reply(request, [finding(), finding(), finding(unit_id="u099")]))
    assert not repeated.findings
    assert {i.code for i in repeated.issues} == {"unknown_unit", "duplicate_unit"}


def test_request_version_changes_with_author_intent_and_selected_units():
    request = prepare(intents=INTENTS)
    same_in_windows = prepare(ORIGINAL.replace("\n", "\r\n"), REVISED, intents=INTENTS)
    assert request.request_id == same_in_windows.request_id
    changed = (ReviewIntent("i1", "允许扩大到全市。"),)
    assert not request.matches(ORIGINAL, REVISED, changed)
    assert request.request_id != prepare(intents=changed).request_id
    subset = prepare(intents=INTENTS, unit_ids=())
    assert subset.unchecked_unit_ids == ("u001",) and not subset.unit_ids
    assert request.request_id != subset.request_id
    with pytest.raises(ScopeReviewError, match="版本"):
        request.parse_reply(reply(request, [finding()], request_id=subset.request_id))
    with pytest.raises(ScopeReviewError, match="JSON"):
        request.parse_reply("不是 JSON")


def test_single_trailing_semicolon_is_recoverable_but_a_second_object_is_not():
    request = prepare(intents=INTENTS)
    raw = reply(request, [finding()])
    assert request.parse_reply(raw + ";").findings[0].kind == "widened"
    with pytest.raises(ScopeReviewError, match="JSON"):
        request.parse_reply(raw + ";{}")


def test_unrelated_but_exact_quotes_are_rejected():
    request = prepare(intents=INTENTS)
    entry = finding(evidence=[{"paragraph": key, "quote": "结果来自东区的两次调查。", "role": "claim"}
                              for key in ("o1", "r1")])
    result = request.parse_reply(reply(request, [entry]))
    assert not result.findings
    assert "没有重叠" in result.issues[0].detail


def test_late_byok_response_cannot_be_returned_to_replaced_draft():
    from workbench.scope_review import run_scope_review

    request = prepare(intents=INTENTS)
    current = True

    class Connection:
        async def generate(self, prompt, *, timeout):
            nonlocal current
            assert prompt == request.prompt and timeout == 5
            current = False
            return reply(request, [finding()])

    with pytest.raises(ScopeReviewError, match="旧结果已丢弃"):
        asyncio.run(run_scope_review(Connection(), request, is_current=lambda: current, timeout=5))


def test_connection_timeout_can_be_retried_without_a_decision():
    from workbench.scope_review import run_scope_review

    request = prepare(intents=INTENTS)

    class Connection:
        calls = 0
        async def generate(self, prompt, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError
            return reply(request, [finding()])

    connection = Connection()
    with pytest.raises(TimeoutError):
        asyncio.run(run_scope_review(connection, request, is_current=lambda: True))
    result = asyncio.run(run_scope_review(connection, request, is_current=lambda: True))
    assert result.findings[0].change_ids == ("c001",)


def test_cross_round_relocation_preserves_uncertainty():
    request = prepare(intents=INTENTS)
    result = request.parse_reply(reply(request, [finding()]))
    old = result.findings[0].evidence[1]

    moved = "本结论适用于全市。\n\n结果来自东区的两次调查。"
    match = relink_evidence(REVISED, moved, old)
    assert match.status == "located" and match.paragraph == 1
    assert moved[match.start:match.end] == old.quote

    changed = relink_evidence(REVISED, REVISED + "该结论限于工作日。", old)
    assert changed.status == "changed_context" and changed.paragraph == 2
    assert relink_evidence(REVISED, REVISED + "\n\n本结论适用于全市。", old).status == "ambiguous"
    assert relink_evidence(REVISED, "结果来自东区的两次调查。", old).status == "missing"

    split = "结果来自东区的两次调查。\n\n本结论适用于全市。仍需核对西区。"
    assert relink_evidence(REVISED, split, old).status == "changed_context"


def test_evaluation_records_missing_results_and_false_scope_changes():
    suite = prepare_cases(CASES / "evaluation.json")
    outputs = {info["case_id"]: {"raw": reply(request, [])} for info, request in suite.cases}
    score = score_run(suite, {"source": "fixture", "model": "missing-fixture", "suite_sha256": suite.sha256,
                             "outputs": outputs})
    assert score["totals"]["expected"] == 6
    assert score["totals"]["valid_findings"] == 0
    assert score["totals"]["missed_scope_changes"] == 3
    assert score["totals"]["manual_review"] == 6
    assert score["totals"]["false_scope_changes"] == 0


def test_scope_review_uses_actual_byok_transport_to_a_local_mock():
    from aiohttp import web

    from workbench.model_connection import Connection
    from workbench.scope_review import run_scope_review

    async def exercise():
        request = prepare(intents=INTENTS)
        received = []

        async def completion(message):
            data = await message.json()
            received.append(data["messages"][0]["content"])
            return web.json_response({"choices": [{"finish_reason": "stop",
                                      "message": {"content": reply(request, [finding()])}}]})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", completion)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            connection = Connection.api("compatible", "gpt-6-luna", f"http://127.0.0.1:{port}/v1", "")
            result = await run_scope_review(connection, request, is_current=lambda: True, timeout=5)
            assert received == [request.prompt]
            assert result.findings[0].evidence[1].quote == "本结论适用于全市。"
            assert not result.issues
        finally:
            await runner.cleanup()

    asyncio.run(exercise())


def test_evaluation_manifest_keeps_fixture_reference_out_of_prompt():
    suite = prepare_cases(CASES / "evaluation.json")
    info, request = suite.cases[0]
    assert "expected" not in request.payload
    assert "manual_question" not in request.payload
    assert info["expected"] == {"u001": ["widened", "insufficient"]}
    assert request.payload["units"][0]["changes"][0]["revised"]["paragraph"] == "r1"


def test_shared_exchange_is_valid_for_the_packaged_skill():
    exchange = json.loads((CASES / "exchange.json").read_text())
    suite = prepare_cases(CASES / "development.json")
    _, request = suite.cases[0]
    assert exchange["request"] == request.payload
    result = request.parse_reply(json.dumps(exchange["response"], ensure_ascii=False))
    assert not result.issues and result.findings[0].kind == "widened"


def test_confirmation_suite_uses_different_manuscripts():
    evaluation = prepare_cases(CASES / "evaluation.json")
    confirmation = prepare_cases(CASES / "confirmation.json")
    development = prepare_cases(CASES / "development.json")
    seen = set()
    for suite in (evaluation, confirmation, development):
        texts = {request.original_sha256 for _, request in suite.cases}
        assert not seen.intersection(texts)
        seen.update(texts)
