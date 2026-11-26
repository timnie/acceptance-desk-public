"""Win32 profile locking, window activation and Edge WebView2 integration."""
import ctypes
import errno
import msvcrt
import os
import re
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from desktop.launcher import BUNDLE_ID, NAME

GUI = "edgechromium"
WEB_STORAGE = "WebView2"
RUNTIME_MISSING = "找不到 Microsoft Edge WebView2 运行时。请通过安装程序补装后重试。"

user32 = ctypes.WinDLL("user32", use_last_error=True)
enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.GetPropW.restype = wintypes.HANDLE
user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]


def default_profile():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME


def _window_property(profile):
    return BUNDLE_ID + "." + sha256(os.path.normcase(str(profile)).encode()).hexdigest()


def _find_window(profile):
    property_name = _window_property(profile)
    hwnd = None

    @enum_callback
    def find(candidate, _):
        nonlocal hwnd
        if user32.GetPropW(candidate, property_name):
            hwnd = candidate
            return False
        return True

    user32.EnumWindows(find, 0)
    return hwnd


def _activate(profile):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        hwnd = _find_window(profile)
        if hwnd:
            if user32.IsIconic(hwnd):
                user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
            if not user32.SetForegroundWindow(hwnd):
                user32.FlashWindow(hwnd, True)
            return
        time.sleep(0.1)


@contextmanager
def instance(profile):
    with (profile / "instance.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno != errno.EACCES:
                raise
            _activate(profile)
            yield False
        else:
            try:
                yield True
            finally:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def prepare():
    from webview.guilib import initialize

    if initialize(GUI).renderer != GUI:
        raise RuntimeError(RUNTIME_MISSING)
    if getattr(sys, "frozen", False):
        # Child CLIs should load system libraries independently of PyInstaller's directory.
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def bind(window, profile):
    property_name = _window_property(profile)

    def register():
        if not user32.SetPropW(window.native.Handle.ToInt64(), property_name, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    window.events.before_show += register


def save_download(window, content: bytes, filename: str) -> None:
    from System import Action
    from webview import FileDialog

    selected = None
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)

    def choose_path():
        nonlocal selected
        selected = window.create_file_dialog(FileDialog.SAVE, directory=str(Path.home() / "Downloads"),
                                             save_filename=filename)

    window.native.Invoke(Action(choose_path))
    if not selected:
        return
    target = Path(selected[0])
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_warning(window, message):
    user32.MessageBoxW(window.native.Handle.ToInt64(), message, NAME, 0x30)


def startup_error(error):
    user32.MessageBoxW(None, str(error), "无法启动工作台 · " + NAME, 0x10)
