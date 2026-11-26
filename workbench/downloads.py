"""Send the same export bytes to the active platform's save action."""
from nicegui import run, ui


async def download_content(content: str | bytes, filename: str, media_type: str = "") -> None:
    save = getattr(ui.context.client, "desktop_download", None)
    if save is None:
        ui.download.content(content, filename, media_type)
        return
    try:
        await run.io_bound(save, content.encode("utf-8") if isinstance(content, str) else content, filename)
    except (OSError, RuntimeError):
        ui.notify("文件未能保存。请检查目标目录权限或磁盘空间后重试。", type="negative")
