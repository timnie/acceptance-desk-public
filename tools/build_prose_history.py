"""重建浏览器文件：uv run --with transcrypt==3.9.5 python tools/build_prose_history.py。

运行产品无需 Transcrypt 或外网。Python 是唯一手写的程序源；生成文件随包发布。
"""
from pathlib import Path
from importlib.metadata import version
import hashlib
import json
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'workbench/assets/prose'
assert version('transcrypt') == '3.9.5', 'Use the pinned Transcrypt 3.9.5 build command.'
with tempfile.TemporaryDirectory(prefix='prose-python-') as temporary:
    build = Path(temporary)
    subprocess.run([sys.executable, '-m', 'transcrypt', '-b', '-n', '-xp', str(ROOT / 'workbench'),
                    '-od', str(build), str(ROOT / 'workbench/browser/prose_history.py')], check=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in ('prose_history.js', 'history_policy.js', 'org.transcrypt.__runtime__.js', 'copy.js'):
        code = (build / name).read_text(encoding='utf-8')
        code = re.sub(r"// Transcrypt'ed from Python, .*\n", '// Generated from Python by Transcrypt 3.9.5. DO NOT EDIT.\n', code)
        code = re.sub(r'\n//# sourceMappingURL=.*', '', code)
        if name == 'prose_history.js':
            code = code.replace("from './cm.js'", "from 'nicegui-codemirror'")
        (TARGET / name).write_text(code, encoding='utf-8')
    sources = ('workbench/browser/prose_history.py', 'workbench/browser/cm.py', 'workbench/history_policy.py')
    manifest = {
        'compiler': 'Transcrypt 3.9.5',
        'sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        'assets': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(TARGET.glob('*.js'))},
    }
    (TARGET / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
