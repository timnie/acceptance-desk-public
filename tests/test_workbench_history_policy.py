from workbench.history_policy import history_boundary
from pathlib import Path
import hashlib
import json


def test_each_chinese_commit_starts_a_new_undo_step():
    assert history_boundary('input.type.compose.start', 'input.type.compose', False) == 'before'
    assert history_boundary('input.type.compose', 'input.type.compose.start', True) is None
    assert history_boundary('input.type.compose', 'input.type.compose', True) is None


def test_paste_replace_and_selected_delete_are_atomic():
    for event in ('input.paste', 'input.drop', 'input.type', 'delete.backward', 'delete.cut'):
        assert history_boundary(event, 'input.type', True) == 'full'
    assert history_boundary('input.paste', '', False) == 'full'


def test_plain_typing_keeps_normal_grouping_but_does_not_join_chinese():
    assert history_boundary('input.type', 'input.type', False) is None
    assert history_boundary('delete.backward', 'delete.backward', False) is None
    assert history_boundary('input.type', 'input.type.compose', False) == 'before'


def test_browser_build_matches_python_sources_and_generated_assets():
    root = Path(__file__).resolve().parents[1]
    assets = root / 'workbench/assets/prose'
    manifest = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['compiler'] == 'Transcrypt 3.9.5'
    for name, digest in manifest['sources'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, 'Rebuild the browser adapter after editing Python.'
    for name, digest in manifest['assets'].items():
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, 'Do not hand-edit generated browser assets.'
