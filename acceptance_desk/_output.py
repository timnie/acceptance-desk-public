"""Create a UTF-8 output file without replacing an existing file or link."""
import os
from pathlib import Path


def write_new_text(path: Path, content: str) -> None:
    if os.name != "nt":
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    # CREATE_NEW with FILE_FLAG_OPEN_REPARSE_POINT rejects dangling symlinks on Windows.
    handle = create_file(str(path), 0x40000000, 3, None, 1, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error(), f"无法独占创建文件：{path}")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
