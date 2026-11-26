"""练习模型连接和轮次接续，测试只使用虚构文稿与本机模拟服务。"""

import asyncio
import json
import os
import sys

import pytest

from acceptance_desk import audit
from review_ui import state
from workbench.next_round import DEFAULT_REVISION_OPTIONS, RevisionRequest
from workbench.session import STORE_KEY, Session

ORIGINAL = "发出三份问卷。样本来自访谈。"
REVISED = "发出五份问卷。样本来自调查。"
CANDIDATE = "发出六份问卷。样本来自访谈。"


def report(original, revised):
    return audit(original, revised, mode="rules_only")


def test_pending_note_can_request_a_candidate_without_deciding_the_change():
    session = Session()
    session.load(ORIGINAL, REVISED, report(ORIGINAL, REVISED), title="虚构示例", names={}, formats={})
    note = "请在第一句后加一句说明这三份问卷只用于演练。"
    state.set_note(session.review, "c001", note)

    omitted = RevisionRequest.prepare(session, DEFAULT_REVISION_OPTIONS)
    included = RevisionRequest.prepare(session, {**DEFAULT_REVISION_OPTIONS, "pending": True})
    assert omitted.pending == included.pending == 2
    assert omitted.author_notes == included.author_notes
    assert tuple(task.change_id for task in omitted.author_notes) == ("c001",)
    assert note in omitted.prompt
    assert note in included.prompt
    assert "严格执行下方作者修改建议" in included.feedback
    assert "此处没有作者建议，仅提供上下文" in included.feedback
    assert "c002 ·" not in omitted.feedback
    assert "请等待作者确认" not in included.feedback
    assert included.baseline == ORIGINAL
    assert not included.confirmed_edits
    assert session.by_id["c001"]["decision"]["status"] == "pending"


def test_next_round_preserves_decisions_and_flags_model_regressions():
    storage = {}
    session = Session(storage)
    session.load(ORIGINAL, REVISED, report(ORIGINAL, REVISED), title="虚构示例", names={}, formats={})
    state.set_decision(session.review, "c001", "rejected")
    state.set_note(session.review, "c001", "数量以原稿为准。")
    session.drafts["c002"] = "待检查问卷来源。"
    request = RevisionRequest.prepare(session, {"policy": "original", "layout": "follow",
                                                "scope": "local", "pending": False})
    assert request.revised == REVISED and request.baseline == ORIGINAL
    assert "数量以原稿为准。" in request.prompt and "待检查问卷来源。" not in request.prompt
    payload = json.loads(request.prompt.split("\n\n", 1)[1])
    assert payload["作者合并稿"] == ORIGINAL
    assert not payload["无新要求处的原有取舍"]
    assert request.author_notes[0].change_id == "c001"
    assert request.is_unchanged(ORIGINAL)
    assert not request.is_unchanged(REVISED)
    assert not request.candidate_issue(CANDIDATE)  # 新建议解除旧取舍的硬限制。
    assert not request.candidate_issue("发出三份问卷。样本来自访谈。样本数量有限。")
    assert request.candidate_issue(f"```text\n{ORIGINAL}\n```")
    assert (request.pending, request.omitted_drafts) == (1, 1)
    state.set_note(session.review, "c001", "决定已经变化。")
    assert not request.matches(session)
    state.set_note(session.review, "c001", "数量以原稿为准。")
    assert request.matches(session)

    session.advance(request.baseline, CANDIDATE, report(request.baseline, CANDIDATE),
                    feedback=request.feedback, destination="模拟连接")
    assert "三份" in session.by_id["c001"]["original"]["text"]
    assert "六份" in session.by_id["c001"]["revised"]["text"]
    assert session.by_id["c001"]["decision"]["status"] == "pending"
    session.persist()
    stored = storage[STORE_KEY]
    assert len(stored["rounds"]) == 1

    reloaded = Session(storage)
    reloaded.restore_project(stored, report(stored["original"], stored["revised"]))
    historical = Session()
    historical.load_snapshot(reloaded.rounds[0], report(ORIGINAL, REVISED))
    assert historical.by_id["c001"]["decision"]["status"] == "rejected"
    assert historical.drafts == {"c002": "待检查问卷来源。"}
    assert "数量以原稿为准。" in reloaded.rounds[0]["sent"]["feedback"]
    reloaded.save_enabled = False
    reloaded.forget()
    assert STORE_KEY not in storage and len(reloaded.rounds) == 1


@pytest.mark.parametrize("decision", ["rejected", "accepted"])
def test_candidate_cannot_restore_explicitly_removed_content(decision):
    short = "本节研究问卷。"
    long = short + "\n\n每份问卷均来自虚构的赫桥地区。"
    original, revised = (short, long) if decision == "rejected" else (long, short)
    session = Session()
    session.load(original, revised, report(original, revised), title="虚构示例", names={}, formats={})
    changed = session.review["changes"][0]
    state.set_decision(session.review, changed["id"], decision)

    request = RevisionRequest.prepare(session, {**DEFAULT_REVISION_OPTIONS, "scope": "rewrite"})

    assert request.baseline.strip() == short
    assert changed["id"] in request.candidate_issue(long)
    assert not request.candidate_issue("本节介绍问卷设计。")


def test_bad_candidate_audit_leaves_current_round_intact():
    from acceptance_desk import ReviewDataError
    session = Session()
    original_report = report(ORIGINAL, REVISED)
    session.load(ORIGINAL, REVISED, original_report, title="虚构示例", names={}, formats={})
    with pytest.raises((ReviewDataError, ValueError, KeyError)):
        session.advance(REVISED, CANDIDATE, original_report, feedback="意见", destination="模拟")
    assert session.revised == REVISED and session.rounds == []


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.skipif(os.name != "posix", reason="模拟 CLI 用 Unix shebang")
def test_local_cli_auth_and_completion(provider, tmp_path):
    pytest.importorskip("aiohttp")
    from workbench.model_connection import Connection
    binary = tmp_path / provider
    binary.write_text(f"#!{sys.executable}\n" + """
import json, sys
if sys.argv[-1] == "status":
    print("Logged in using ChatGPT" if sys.argv[-2] == "login" else '{"authMethod":"claude.ai"}')
elif "debug" in sys.argv:
    print(json.dumps({"models": [
        {"slug": "fixture-codex", "display_name": "Fixture Codex", "supported_reasoning_levels": [{"effort": "high"}]},
        {"slug": "hidden-model", "visibility": "hidden"},
    ]}))
else:
    text = sys.stdin.read()
    assert "虚构测试" in text and "虚构测试" not in sys.argv
    assert sys.argv[sys.argv.index("--model") + 1] in {"fixture-codex", "sonnet"}
    if "exec" in sys.argv:
        assert 'model_reasoning_effort="high"' in sys.argv
        print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":text}}))
        print(json.dumps({"type":"turn.completed"}))
    else:
        print(json.dumps({"subtype":"success","result":text}))
""", encoding="utf-8")
    binary.chmod(0o700)

    async def check():
        connection = Connection(provider, executable=str(binary))
        assert "已登录" in await connection.check_login()
        options, live = await connection.models()
        if provider == "codex":
            assert live and [(item.id, item.efforts) for item in options] == [("fixture-codex", ("high",))]
            connection = connection.with_cli_model("fixture-codex", "Fixture Codex", "high")
            assert "推理强度 高" in connection.destination
        else:
            assert not live and "sonnet" in {item.id for item in options}
            connection = connection.with_cli_model("sonnet", "Sonnet", "")
        assert await connection.generate("虚构测试") == "虚构测试"

    asyncio.run(check())


def test_api_sends_to_selected_model_and_rejects_bad_responses():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from workbench.model_connection import Connection, ConnectionError

    async def check():
        calls = []

        async def reply(request):
            body = await request.json()
            calls.append((request.headers.get("Authorization"), body))
            if body["model"] == "bad-key":
                return web.Response(status=401, text="raw credential diagnostic")
            if body["model"] == "redirect":
                return web.Response(status=307, headers={"Location": request.path})
            reason = "length" if body["model"] == "truncated" else "stop"
            return web.json_response({"choices": [{"finish_reason": reason, "message": {"content": CANDIDATE}}]})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", reply)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            base = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
            connection = Connection.api("compatible", "mock", base, "test-secret")
            assert await connection.generate("虚构文稿") == CANDIDATE
            assert calls[0] == ("Bearer test-secret", {"model": "mock", "messages": [
                {"role": "user", "content": "虚构文稿"}], "stream": False})
            assert "test-secret" not in repr(connection)
            for model, message in [("bad-key", "API Key 无效"), ("truncated", "没有返回完整"),
                                   ("redirect", "HTTP 307")]:
                with pytest.raises(ConnectionError, match=message):
                    await Connection.api("compatible", model, base, "test-secret").generate("虚构文稿")
            assert len(calls) == 4
        finally:
            await runner.cleanup()

    asyncio.run(check())
    for address in ("http://example.org", "https://user:secret@example.org", "https://example.org?token=secret",
                    "https://example.org/v1/chat/completions"):
        with pytest.raises(ConnectionError):
            Connection.api("compatible", "mock", address, "test-secret")


def test_local_cli_timeout_and_cancel_leave_no_waiting_process(tmp_path):
    pytest.importorskip("aiohttp")
    from workbench.model_connection import ConnectionError, _process

    async def check():
        with pytest.raises(ConnectionError, match="超时"):
            await _process(sys.executable, ["-c", "import time; time.sleep(20)"], timeout=0.1)
        task = asyncio.create_task(_process(sys.executable, ["-c", "import time; time.sleep(20)"], timeout=10))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)

    asyncio.run(check())


def test_codex_completed_turn_is_returned_when_cli_keeps_its_pipes_open():
    pytest.importorskip("aiohttp")
    from workbench.model_connection import _codex_result, _process

    script = """
import json, sys, time
document = sys.stdin.buffer.read().decode('utf-8')
event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': document}}
for row in (event, {'type': 'turn.completed'}):
    sys.stdout.buffer.write(json.dumps(row, ensure_ascii=False).encode('utf-8') + b'\\r\\n')
    sys.stdout.buffer.flush()
time.sleep(20)
"""
    document = "虚构正文\u2028" * 10000
    code, stdout, _ = asyncio.run(_process(sys.executable, ["-c", script], prompt=document, timeout=4,
                                          finish_on_codex_turn=True))
    assert code == 0
    assert _codex_result(stdout) == document


@pytest.mark.parametrize(("protocol", "path", "headers", "catalog", "answer"), [
    ("chat", "/chat/completions", {"Authorization": "Bearer fixture-secret"},
     {"data": [{"id": "fixture-model"}]}, {"choices": [{"finish_reason": "stop", "message": {"content": CANDIDATE}}]}),
    ("responses", "/responses", {"Authorization": "Bearer fixture-secret"},
     {"data": [{"id": "fixture-model"}]}, {"status": "completed", "output": [
         {"type": "message", "content": [{"type": "output_text", "text": CANDIDATE}]}]}),
    ("anthropic", "/messages", {"x-api-key": "fixture-secret", "anthropic-version": "2023-06-01"},
     {"data": [{"id": "fixture-model", "display_name": "Fixture"}]},
     {"stop_reason": "end_turn", "content": [{"type": "text", "text": CANDIDATE}]}),
    ("gemini", "/models/fixture-model:generateContent", {"x-goog-api-key": "fixture-secret"},
     {"models": [{"name": "models/fixture-model", "supportedGenerationMethods": ["generateContent"]},
                 {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]},
     {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": CANDIDATE}]}}]}),
    ("azure", "/chat/completions", {"api-key": "fixture-secret"},
     {"data": [{"id": "fixture-model"}]}, {"choices": [{"finish_reason": "stop", "message": {"content": CANDIDATE}}]}),
])
def test_protocol_catalog_and_generation(protocol, path, headers, catalog, answer):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from workbench.model_connection import Connection

    async def check():
        traffic = []

        async def reply(request):
            traffic.append((request.method, request.path))
            assert all(request.headers.get(key) == value for key, value in headers.items())
            if request.method == "GET":
                assert not await request.read()
                return web.json_response(catalog)
            body = await request.json()
            assert "虚构输入" in json.dumps(body, ensure_ascii=False)
            if protocol != "gemini":
                assert body["model"] == "fixture-model"
            return web.json_response(answer)

        app = web.Application()
        prefix = "/openai/v1" if protocol == "azure" else "/fixture"
        app.router.add_get(prefix + "/models", reply)
        app.router.add_post(prefix + path, reply)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            base = f"http://127.0.0.1:{runner.addresses[0][1]}{prefix}"
            connection = Connection.api("compatible", "fixture-model", base, "fixture-secret", protocol=protocol)
            models, live = await connection.models()
            assert live and [model.id for model in models] == ["fixture-model"]
            assert await connection.generate("虚构输入") == CANDIDATE
            assert traffic == [("GET", prefix + "/models"), ("POST", prefix + path)]
        finally:
            await runner.cleanup()

    asyncio.run(check())


def test_preferences_remember_each_provider_without_storing_secrets():
    pytest.importorskip("nicegui")
    from workbench.model_connection import Connection
    from workbench.model_dialogs import ModelDialogs

    store = {}
    models = ModelDialogs(store)
    codex = Connection("codex").with_cli_model("fixture-codex", "Fixture Codex", "high")
    api = Connection.api("compatible", "fixture-api", "http://127.0.0.1:8123/v1", "fixture-secret")
    models.use(codex)
    models.use(api)
    models.use(codex)
    assert models.api_key("compatible", "chat", "http://127.0.0.1:8123/v1/") == "fixture-secret"
    assert models.api_key("compatible", "chat", "http://127.0.0.1:8124/v1") == ""
    assert models.api_key("compatible", "responses", "http://127.0.0.1:8123/v1") == ""
    assert models.api_key("deepseek", "chat", "http://127.0.0.1:8123/v1") == ""
    assert "fixture-secret" not in json.dumps(store)
    models.use(api)
    resumed = ModelDialogs(store)
    assert resumed.connection is None
    assert resumed.api_key("compatible", "chat", "http://127.0.0.1:8123/v1") == ""
    assert resumed.preference("codex")["model"] == "fixture-codex"
    assert resumed.preference("compatible")["base_url"] == "http://127.0.0.1:8123/v1"


@pytest.mark.parametrize("provider,events", [
    ("antigravity", [{"event": "result", "result": {"status": "SUCCESS", "response": CANDIDATE}}]),
    ("cursor-agent", [{"type": "result", "subtype": "success", "is_error": False, "result": CANDIDATE}]),
    ("opencode", [{"type": "text", "part": {"text": CANDIDATE}},
                  {"type": "step_finish", "part": {"reason": "stop"}}]),
    ("copilot", [{"type": "assistant.message_delta", "data": {"deltaContent": CANDIDATE}},
                 {"type": "result", "success": True}]),
    ("qwen-code", [{"type": "result", "subtype": "success", "is_error": False, "result": CANDIDATE}]),
    ("pi", [{"type": "agent_end", "messages": [{"role": "assistant", "stopReason": "stop",
                                                "content": [{"type": "text", "text": CANDIDATE}]}]}]),
])
@pytest.mark.skipif(os.name != "posix", reason="模拟 CLI 用 Unix shebang")
def test_added_cli_runs_with_private_cwd_and_stdin(provider, events, tmp_path):
    pytest.importorskip("aiohttp")
    from workbench.model_cli import CLIS
    from workbench.model_connection import Connection

    cli = CLIS[provider]
    binary = tmp_path / cli.binaries[0]
    test_prompt = "虚构文稿，不进入命令参数"
    binary.write_text(f"#!{sys.executable}\n" + f"""
import json, os, pathlib, sys
if "--help" in sys.argv:
    print({" ".join(cli.flags)!r})
elif sys.argv[1:] == ["status"]:
    print("Logged in")
else:
    assert {test_prompt!r} not in " ".join(sys.argv)
    assert "fixture-model" in sys.argv
    assert os.getcwd() != {str(tmp_path)!r}
    raw = sys.stdin.read()
    if {provider!r} == "antigravity":
        assert json.loads(raw)["message"]["content"] == {test_prompt!r}
        assert json.loads(sys.argv[sys.argv.index("--settings") + 1])["toolPermission"] == "strict"
    else:
        assert raw == {test_prompt!r}
    if {provider!r} == "cursor-agent":
        rules = json.loads(pathlib.Path(".cursor/cli.json").read_text())
        assert "Shell(*)" in rules["permissions"]["deny"]
    for event in {events!r}:
        print(json.dumps(event))
""", encoding="utf-8")
    binary.chmod(0o700)

    async def check():
        connection = Connection(provider, executable=str(binary)).with_cli_model("fixture-model", "", "")
        assert provider == connection.provider and "fixture-model" in connection.destination
        assert await connection.check_login()
        assert await connection.generate(test_prompt) == CANDIDATE

    asyncio.run(check())


def test_cli_rejects_missing_completion_and_bare_tool_output():
    from workbench.model_cli import result

    with pytest.raises(StopIteration):
        result("copilot", '{"type":"assistant.message_delta","data":{"deltaContent":"partial"}}')
    with pytest.raises(ValueError):
        result("opencode", '{"type":"step_finish","part":{"reason":"length"}}')
    with pytest.raises(ValueError, match="authentication"):
        result("antigravity", '{"event":"result","result":{"status":"ERROR","error":"authentication failed"}}')


def test_bedrock_converse_uses_selected_region_and_profile(monkeypatch):
    pytest.importorskip("aiohttp")
    from workbench import model_connection
    from workbench.model_connection import Connection, ConnectionError

    base = "https://bedrock-runtime.us-east-1.amazonaws.com"
    connection = Connection.api("bedrock", "us.anthropic.sonnet-v1:0", base, "test-key")
    with pytest.raises(ConnectionError, match="Bedrock Runtime"):
        Connection.api("bedrock", "sonnet", "https://example.org", "test-key")

    async def mock_api(selected, method, path, timeout, body):
        assert selected.base_url == base
        assert method == "POST" and path == "/model/us.anthropic.sonnet-v1%3A0/converse"
        assert model_connection._api_headers(selected) == {"Authorization": "Bearer test-key"}
        assert body == {"messages": [{"role": "user", "content": [{"text": "虚构文稿"}]}]}
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": CANDIDATE}]}}}

    monkeypatch.setattr(model_connection, "_api_json", mock_api)
    assert asyncio.run(connection.models()) == ([], False)
    assert asyncio.run(connection.generate("虚构文稿")) == CANDIDATE


def test_model_specific_tuning_catalogs():
    from workbench.model_catalog import codex_models
    from workbench.model_cli import model_options

    models = codex_models({"models": [
        {"slug": "fixture", "supported_reasoning_levels": [{"effort": "high"}], "additional_speed_tiers": ["fast"]},
        {"slug": "other", "supported_reasoning_levels": [{"effort": "low"}]},
    ]})
    assert [(m.id, m.efforts, m.tiers) for m in models] == [
        ("fixture", ("high",), ("priority",)), ("other", ("low",), ())]
    options = model_options("opencode", 'lab/model\n{\n  "variants": {"fast-1": {}, "high": {}, "--other": {}}\n}\nlab/plain\n')
    assert [(m.id, m.efforts) for m in options] == [("lab/model", ("fast-1", "high")), ("lab/plain", ())]
    pi = model_options("pi", "provider model context output thinking images\nlab a 100k 32k yes no\nlab b 100k 32k no yes")
    assert "xhigh" in pi[0].efforts and not pi[1].efforts


@pytest.mark.parametrize("provider,model,effort,expected,response", [
    ("openai", "gpt-5.4", "xhigh", {"reasoning": {"effort": "xhigh"}},
     {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": CANDIDATE}]}]}),
    ("gemini", "gemini-3-flash", "low", {"generationConfig": {"thinkingConfig": {"thinkingLevel": "LOW"}}},
     {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": CANDIDATE}]}}]}),
    ("anthropic", "claude-sonnet-4-6", "high", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
     {"stop_reason": "end_turn", "content": [{"type": "text", "text": CANDIDATE}]}),
    ("deepseek", "deepseek-flash", "none", {"reasoning_effort": "none"},
     {"choices": [{"finish_reason": "stop", "message": {"content": CANDIDATE}}]}),
])
def test_api_thinking_is_sent_to_the_right_protocol(provider, model, effort, expected, response, monkeypatch):
    pytest.importorskip("aiohttp")
    from workbench import model_connection as conn

    async def mock_api(connection, method, path, timeout, body):
        assert method == "POST"
        assert {key: body[key] for key in expected} == expected
        return response

    monkeypatch.setattr(conn, "_api_json", mock_api)
    connection = conn.Connection.api(provider, model, conn.API_PRESETS[provider][2], "test-key", effort=effort)
    assert asyncio.run(connection.generate("虚构文稿")) == CANDIDATE


@pytest.mark.parametrize("provider,model,effort,flag,catalog,reply", [
    ("opencode", "lab/model", "research", "--variant",
     'lab/model\n{"variants":{"research":{}}}', '{"type":"text","part":{"text":"成稿"}}\n{"type":"step_finish","part":{"reason":"stop"}}'),
    ("pi", "lab/model", "high", "--thinking", "lab model 100k 32k yes no",
     '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"成稿"}]}]}'),
])
def test_cli_passes_effort_and_rejects_retired_model_options(provider, model, effort, flag, catalog, reply, monkeypatch):
    pytest.importorskip("aiohttp")
    from workbench import model_connection as conn
    from workbench.model_cli import CLIS

    calls = []
    async def mock_process(executable, args, **kwargs):
        calls.append(args)
        if "--help" in args:
            return 0, " ".join((*CLIS[provider].flags, flag)), ""
        if tuple(args) in (CLIS[provider].catalog, CLIS[provider].basic_catalog):
            return 0, catalog, ""
        assert args[args.index(flag) + 1] == effort
        assert kwargs["prompt"] == "虚构文稿"
        return 0, reply, ""

    monkeypatch.setattr(conn, "_process", mock_process)
    connection = conn.Connection(provider, executable="/fixture").with_cli_model(model, "", effort)
    assert asyncio.run(connection.generate("虚构文稿")) == "成稿"
    count = len(calls)
    catalog = ""
    with pytest.raises(conn.ConnectionError, match="所选档位"):
        asyncio.run(connection.generate("虚构文稿"))
    assert all(flag not in args for args in calls[count:])


def test_local_candidate_preserves_blank_line_pattern():
    session = Session()
    original = "第一段保持不变。\n第二段说明原始证据。\n\n末段保持不变。\n"
    revised = original.replace("原始证据", "试验依据")
    session.load(original, revised, report(original, revised), title="示例", names={}, formats={})
    request = RevisionRequest.prepare(session, {"policy": "original", "layout": "follow",
                                                "scope": "local", "pending": False})
    candidate = "第一段保持不变。\n\n第二段补充证据。\n末段保持不变。"
    assert request.preserve_local_layout(candidate) == "第一段保持不变。\n第二段补充证据。\n\n末段保持不变。\n"


def test_cli_and_api_revision_payload_uses_authors_edited_instructions():
    session = Session()
    session.load(ORIGINAL, REVISED, report(ORIGINAL, REVISED), title="示例", names={}, formats={})
    note = "保留一切引注，仅修正第二段中归因不清之处。"
    request = RevisionRequest.prepare(session, {"policy": "original", "layout": "follow",
                                                "scope": "local", "pending": False, "instructions": note})
    contract, data = request.prompt.split("\n\n", 1)
    assert "作者亲自写的修改建议和修订指令是本次任务的最高优先级" in contract
    assert json.loads(data)["本次修订指令"] == note
