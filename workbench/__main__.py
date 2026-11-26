"""启动工作台：uv run --extra web python -m workbench。默认只监听本机 127.0.0.1:8780。"""

import os
from pathlib import Path

# 每个浏览器的进度存在用户主目录下，不放在仓库目录里；必须在导入 nicegui 之前设置
storage_path = os.environ.setdefault("NICEGUI_STORAGE_PATH", str(Path.home() / ".acceptance-desk" / "web-storage"))
if not os.environ.get("NICEGUI_REDIS_URL"):
    try:
        Path(storage_path).resolve().mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        # 目录不可写时仍允许审阅，由现有保存状态提示落盘失败。
        pass

from .app import main  # noqa: E402

if __name__ in {"__main__", "__mp_main__"}:
    main()
