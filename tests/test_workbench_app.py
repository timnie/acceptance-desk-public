"""可选 web 依赖装好后，实际构造载入页和两种工作台，捕获框架 API/闭包接线错误。"""

import importlib.util
import subprocess
import sys

import pytest


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_workbench_can_save_to_a_new_nested_storage_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("NICEGUI_STORAGE_PATH", str(tmp_path / "profile" / "storage"))
    monkeypatch.delenv("NICEGUI_REDIS_URL", raising=False)
    code = '''
import json
import workbench.__main__
from nicegui import app

storage = app.storage.general
storage["检查"] = "已保存"
assert json.loads(storage.filepath.read_text(encoding="utf-8"))["检查"] == "已保存"
'''
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_workbench_views_can_be_constructed():
    code = '''
from nicegui import ui
from acceptance_desk import audit
from workbench.app import import_view, workbench_view
from workbench.session import Session

s = Session({})
with ui.column():
    import_view(s, lambda *_: None)
for original, revised, fmt in [
    ("研究可能有效。", "研究有效。", "txt"),
    ("研究可能有效。\\n    样本来自访谈。", "研究可能有效。\\n  样本来自访谈。", "md"),
]:
    s.load(original, revised, audit(original, revised, mode="rules_only"),
           title="虚构示例", names={}, formats={"original": fmt, "revised": fmt})
    for display in ("proof", "compare"):
        s.display = display
        with ui.column():
            workbench_view(s, lambda: None)
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_appearance_selection_preserves_document_and_restores_explicit_and_system_modes():
    code = '''
from nicegui import ui
from workbench.app import APPEARANCE_KEY, appearance_menu

saved_document = {"original": "原稿", "review": {"decision": "rejected"}}
storage = {"workbench": saved_document, APPEARANCE_KEY: None}
dark = ui.dark_mode(None).bind_value(storage, APPEARANCE_KEY)
selector = appearance_menu(dark)
assert selector.value == "system"
for choice, expected in [("dark", True), ("light", False), ("system", None)]:
    selector.value = choice
    assert dark.value is expected
    assert storage[APPEARANCE_KEY] is expected
    assert storage["workbench"] is saved_document
    restored = ui.dark_mode(None).bind_value(storage, APPEARANCE_KEY)
    assert restored.value is expected
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_autosave_failure_can_be_retried_without_losing_draft():
    code = '''
from nicegui import ui
from acceptance_desk import audit
from workbench.session import Session, STORE_KEY
from workbench.saving import Autosave

class BrokenStorage(dict):
    def __setitem__(self, key, value):
        raise OSError("disk full")
s = Session(BrokenStorage())
s.load("研究可能有效。", "研究有效。", audit("研究可能有效。", "研究有效。", mode="rules_only"),
       title="示例", names={}, formats={})
s.drafts["c001"] = "保留的草稿"
saver = Autosave(s)
saver.changed()
assert saver.message == "保存中…"
saver.save()
assert saver.message == "保存失败 · 重试" and not saver.confirmed
assert s.drafts["c001"] == "保留的草稿"
s.storage = {}
saver.save()
assert s.storage[STORE_KEY]["workspace"]["drafts"]["c001"] == "保留的草稿"
s.save_enabled = False
saver.save()
import asyncio
asyncio.run(saver._tick())
assert saver.message == "仅本页保存" and saver.confirmed
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_autosave_requires_disk_confirmation_and_ignores_stale_confirmation():
    code = '''
import asyncio
import json
import tempfile
from pathlib import Path
from acceptance_desk import audit
from workbench.session import Session
from workbench.saving import Autosave
import workbench.saving as saving

class Storage(dict):
    pass
with tempfile.TemporaryDirectory() as directory:
    storage = Storage()
    storage.filepath = Path(directory) / "user.json"
    storage.filepath.write_text("{}")
    s = Session(storage)
    s.load("研究可能有效。", "研究有效。", audit("研究可能有效。", "研究有效。", mode="rules_only"),
           title="示例", names={}, formats={})
    saver = Autosave(s)
    async def read(function, path):
        return function(path)
    saving.run.io_bound = read
    saver.save()
    assert not asyncio.run(saver.wait_for_confirmation(timeout=0))
    saver.deadline = 0
    asyncio.run(saver._tick())
    assert saver.message == "保存失败 · 重试" and not asyncio.run(saver.wait_for_confirmation(timeout=0))
    saver.save()
    storage.filepath.write_text(json.dumps(storage))
    asyncio.run(saver._tick())
    assert saver.message == "已保存" and asyncio.run(saver.wait_for_confirmation(timeout=0))
    s.save_enabled = False
    saver.save()
    assert not asyncio.run(saver.wait_for_confirmation(timeout=0))
    saver.deadline = 0
    asyncio.run(saver._tick())
    assert saver.message == "保存失败 · 重试" and not asyncio.run(saver.wait_for_confirmation(timeout=0))
    saver.save()
    storage.filepath.write_text(json.dumps(storage))
    asyncio.run(saver._tick())
    assert saver.message == "仅本页保存" and saver.confirmed
    s.save_enabled = True
    saver.save()
    revision = saver.confirm_revision
    async def stale(function, path):
        saver.changed()
        return revision
    saving.run.io_bound = stale
    asyncio.run(saver._tick())
    assert saver.dirty and saver.message == "保存中…" and not saver.confirmed
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.skipif(importlib.util.find_spec("nicegui") is None, reason="需要可选 web 依赖")
def test_revision_layout_switches_prepare_once_with_the_last_choice():
    code = '''
import asyncio
import html
from acceptance_desk import audit
from nicegui import core, ui
from nicegui.events import GenericEventArguments, handle_event
from workbench.model_connection import Connection
from workbench.model_dialogs import ModelDialogs
from workbench.next_round import DEFAULT_REVISION_OPTIONS, RevisionRequest
from workbench.session import Session

async def check():
    core.loop = asyncio.get_running_loop()
    original, revised = "研究可能有效。\\n\\n以下是证据。", "研究有效。\\n以下是证据。"
    session = Session()
    session.load(original, revised, audit(original, revised, mode="rules_only"),
                 title="虚构稿", names={}, formats={})
    options = dict(DEFAULT_REVISION_OPTIONS)
    request = RevisionRequest.prepare(session, options)
    assert request.has_layout_diffs
    models = ModelDialogs()
    models.use(Connection("codex"))
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def prepare(choice):
        calls.append(choice)
        started.set()
        await release.wait()
        updated = RevisionRequest.prepare(session, choice)
        finished.set()
        return updated

    with ui.column() as root:
        dialog = models.revision(request, options=options, prepare=prepare,
                                 matches=lambda item: item.matches(session),
                                 advance=None, filename="候选稿.txt")

    def find(label):
        return next(e for e in dialog.descendants()
                    if getattr(e, "content", None) == label or getattr(e, "text", None) == label)

    def click(label):
        element = find(label)
        listener = next(event for event in element._event_listeners.values() if event.type == "click")
        handle_event(listener.handler, GenericEventArguments(sender=element, client=root.client, args=[]))

    for label in ("改稿排版", "跟随决定", "原稿排版"):
        click(label)
        assert find(label).props["aria-pressed"] == "true"
    assert not calls

    click("确认")
    await asyncio.wait_for(started.wait(), 2)
    assert calls == [{**DEFAULT_REVISION_OPTIONS, "layout": "original"}]
    await asyncio.sleep(0)
    assert find("正在准备对照稿……")
    release.set()
    await asyncio.wait_for(finished.wait(), 2)
    await asyncio.sleep(0)
    assert find("核对并发送")
    prepared = RevisionRequest.prepare(session, calls[0])
    assert prepared.baseline == original
    assert find(html.escape(prepared.baseline))

asyncio.run(check())
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
