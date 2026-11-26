"""Exercise the installed Windows application and its native window lifecycle."""
import argparse
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import Error, expect, sync_playwright

from acceptance_desk import merge_decisions
from check_prose_editor import check_chinese_undo
from review_ui.state import export_names

TITLE = "AI 改稿验收台"
WM_CLOSE = 0x0010
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def wait_for(check, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError(f"Timed out waiting for {description}")


def native_windows(pid):
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    windows = []

    @callback_type
    def find(candidate, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(candidate, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(candidate):
            title = ctypes.create_unicode_buffer(256)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(candidate, title, len(title))
            user32.GetClassNameW(candidate, class_name, len(class_name))
            windows.append((candidate, title.value, class_name.value))
        return True

    user32.EnumWindows(find, 0)
    return windows


def native_window(pid):
    return next((hwnd for hwnd, title, _ in native_windows(pid) if title == TITLE), None)


def user_profile():
    # A process started with alternate credentials can inherit the caller's environment.
    # Ask Windows for the profile of the account actually running this test.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    open_token = ctypes.WinDLL("advapi32", use_last_error=True).OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    profile_path = ctypes.WinDLL("userenv", use_last_error=True).GetUserProfileDirectoryW
    profile_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not open_token(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        path = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(len(path))
        if not profile_path(token, path, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(path.value)
    finally:
        kernel32.CloseHandle(token)


def launch(executable, profile, playwright):
    with socket.socket() as listener:
        for port in range(9222, 9250):
            try:
                listener.bind(("127.0.0.1", port))
                break
            except OSError:
                continue
        else:
            raise RuntimeError("No free WebView2 debugging port")
    env = {
        **os.environ,
        "PATH": os.pathsep.join((str(profile.parent / "model-cli"), os.environ["PATH"])),
        "ACCEPTANCE_DESK_DESKTOP_PROFILE": str(profile),
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}",
    }
    process = subprocess.Popen([str(executable)], env=env, cwd=profile.parent)
    try:
        window = wait_for(lambda: native_window(process.pid), "the native window")

        endpoint = f"http://127.0.0.1:{port}"
        opener = build_opener(ProxyHandler({}))
        last_error = None

        def connect():
            nonlocal last_error
            try:
                with opener.open(f"{endpoint}/json/version", timeout=1) as response:
                    debugger = json.load(response)["webSocketDebuggerUrl"]
                return playwright.chromium.connect_over_cdp(debugger, timeout=3000, no_defaults=True)
            except (Error, OSError, ValueError, KeyError) as error:
                last_error = error
                return None

        try:
            browser = wait_for(connect, "the embedded WebView2")
        except AssertionError as error:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | "
                            "Where-Object { $_.CommandLine -notmatch ' --type=' } | ForEach-Object { "
                            "Write-Host ($_.CommandLine -replace '^.+?(?= --)', 'WebView2'); "
                            "Get-NetTCPConnection -OwningProcess $_.ProcessId -State Listen "
                            "-ErrorAction SilentlyContinue | Format-Table LocalAddress,LocalPort }"],
                           timeout=15, check=False)
            raise AssertionError(f"{error}: {last_error}") from last_error
        context = browser.contexts[0]
        try:
            page = context.pages[0] if context.pages else context.wait_for_event("page", timeout=15000)
            page.wait_for_url("http://127.0.0.1:*/", timeout=30000, wait_until="domcontentloaded")
            page.set_default_timeout(8000)
            page.wait_for_function("window.did_handshake && window.socket?.connected")
        except Error:
            with opener.open(f"{endpoint}/json/list", timeout=3) as response:
                targets = json.load(response)
            print("WebView2 targets:", [(t.get("type"), re.sub("/open/.*", "/open/[redacted]", t.get("url", "")))
                                        for t in targets], file=sys.stderr, flush=True)
            raise
        return process, window, page, env
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait(10)
        raise


def close_app(process, window):
    post_window_message(window, WM_CLOSE)
    try:
        assert process.wait(20) == 0
    except subprocess.TimeoutExpired:
        print("Windows still open after the close request:", native_windows(process.pid), file=sys.stderr, flush=True)
        raise


def post_window_message(window, message, wparam=0, lparam=0):
    post = ctypes.windll.user32.PostMessageW
    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    assert post(window, message, wparam, lparam)


def button(page, label):
    return page.get_by_role("button", name=label, exact=True)


def setup_test_model(root):
    cli = root / "model-cli"
    entry = cli / "node_modules" / "draft-fixture" / "bin.js"
    entry.parent.mkdir(parents=True)
    (cli / "codex.cmd").write_text(
        '@ECHO off\nSET "dp0=%~dp0"\nSET "_prog=node"\n'
        '"%_prog%" "%dp0%\\node_modules\\draft-fixture\\bin.js" %*\n',
        encoding="utf-8",
    )
    entry.write_text(
        """const fs = require('node:fs');
const args = process.argv.slice(2);
if (args.join(' ') === 'login status') {
  console.log('Logged in using ChatGPT');
} else if (args.includes('exec')) {
  if (!args.includes('--ignore-user-config') || process.env.OPENAI_API_KEY) process.exit(2);
  const input = fs.readFileSync(0, 'utf8');
  let text = 'OK';
  if (input !== '连接测试。请只回复 OK。') {
    const {作者合并稿: baseline, 必须逐条执行的作者要求: tasks} = JSON.parse(input.split('\\n\\n').at(-1));
    if (!baseline.endsWith("\\n\\n")) process.exit(3);
    text = JSON.stringify({
      document: baseline.trimEnd() + '\\n\\n在此补写一句验收专用说明。\\n',
      executions: tasks.map(({id}) => ({id, status: 'done', detail: '已在文末补写一句说明。'})),
    });
  }
  console.log(JSON.stringify({type: 'item.completed', item: {type: 'agent_message', text}}));
  console.log(JSON.stringify({type: 'turn.completed'}));
  if (input !== '连接测试。请只回复 OK。') setTimeout(() => {}, 30000);
} else {
  process.exit(2);
}
""",
        encoding="utf-8",
    )


def check_next_round(page):
    button(page, "生成新稿").click()
    layout = page.get_by_role("group", name="对照稿的排版")
    for label in ("原稿排版", "跟随决定", "改稿排版"):
        choice = layout.get_by_role("button", name=label, exact=True)
        choice.click()
        expect(choice).to_have_attribute("aria-pressed", "true")
    button(page, "下一步：模型").click()
    button(page, "测试连接").click()
    expect(page.get_by_text("模型返回了文本。", exact=False)).to_be_visible()
    button(page, "使用此连接").click()
    button(page, "确认发送").click()
    expect(page.get_by_text("候选稿预览", exact=True)).to_be_visible()
    expect(page.get_by_text("已在文末补写一句说明。", exact=True)).to_be_visible()
    button(page, "开始下一轮验收").click()
    expect(button(page, "切换审阅轮次")).to_have_text("第 2 轮")


def export_file(page, pid, label, destination):
    # Interact with the actual WinForms file dialog outside the embedded page.
    assert not destination.exists()
    button(page, label).click()
    try:
        handle = wait_for(lambda: next((hwnd for hwnd, _, cls in native_windows(pid) if cls == "#32770"), None),
                          "the native Save As window", timeout=18)
    except AssertionError:
        print("Visible application windows:", native_windows(pid), file=sys.stderr, flush=True)
        raise
    user32 = ctypes.windll.user32
    user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetDlgItem.restype = wintypes.HWND
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    save = user32.GetDlgItem(handle, 1)
    assert save, "The standard Save As dialog did not display its Save button"
    result = ctypes.c_size_t()
    assert user32.SendMessageTimeoutW(handle, 0x0028, save, 1, 2, 2000, ctypes.byref(result))  # WM_NEXTDLGCTL
    post_window_message(save, 0x00F5)  # BM_CLICK
    wait_for(lambda: not user32.IsWindow(handle), "the native Save As dialog to close")
    try:
        wait_for(lambda: destination.is_file() and destination.stat().st_size, f"downloaded {destination.name}")
    except AssertionError:
        print("Visible application windows:", native_windows(pid), file=sys.stderr, flush=True)
        for folder in (destination.parent, Path.home() / "Downloads", Path.home() / "Documents"):
            print("Export directory entries:", folder, [file.name for file in folder.glob("*.json*")],
                  file=sys.stderr, flush=True)
        print("Save failure notice:", page.get_by_text("文件未能保存", exact=False).count(), file=sys.stderr, flush=True)
        raise


def open_long_example(page):
    consent = button(page, "跳过")
    expect(consent).to_be_visible()
    consent.click()
    assert "/open/" not in page.url
    expect(button(page, "模型连接")).to_be_visible()
    page.get_by_text("打开示例", exact=True).click()
    button(page, "长文示例").click()
    progress = page.locator(".wb-prog")
    expect(progress).to_contain_text("0 / 13")
    return progress


def check_review(page, root):
    button(page, "跳过").click()
    upload_long_documents(page, root / "排版验收", revised_suffix="\n")
    button(page, "开始检查").click()
    progress = page.locator(".wb-prog")
    expect(progress).to_contain_text("0 / 13")
    accept = button(page, "采用改稿")
    accept_id = accept.get_attribute("id")
    button(page, "并排对校").click()
    expect(page.locator(".wb-compare")).to_be_visible()
    button(page, "连续正文").click()
    expect(page.locator(".wb-compare")).to_have_count(0)
    expect(accept).to_have_attribute("id", accept_id)

    untouched_row = page.locator(".wb-list .wb-row").last
    row_id = untouched_row.get_attribute("id")
    accept.click()
    expect(progress).to_contain_text("1 / 13")
    expect(untouched_row).to_have_attribute("id", row_id)
    undo = button(page, "撤销")
    expect(undo).to_have_attribute("title", re.compile(r"Ctrl\+Z"))
    decision_heading = page.locator(".wb-decision-title")
    decision_heading.press("Control+z")
    expect(progress).to_contain_text("0 / 13")
    decision_heading.press("Control+y")
    expect(progress).to_contain_text("1 / 13")
    expect(button(page, "采用改稿")).to_have_attribute("aria-pressed", "true")
    pending = page.locator(".wb-list .wb-row:not(.done)").first
    if not pending.is_visible():
        button(page, "修改目录").click()
    pending.click()
    expect(page.locator(".wb-decision-title .wb-state")).to_have_class("wb-state pending")
    expect(untouched_row).to_have_attribute("id", row_id)
    button(page, "保留原稿").click()
    expect(progress).to_contain_text("2 / 13")

    expect(button(page, "生成新稿")).to_be_visible()

    editor = page.locator(".wb-fb .cm-content")
    editor.click()
    check_chinese_undo(page, editor, "Control")
    page.keyboard.insert_text("关闭前保存中文修改意见。")
    expect(editor).to_have_text("关闭前保存中文修改意见。")


def check_native_export(page, pid):
    progress = open_long_example(page)
    button(page, "采用改稿").click()
    expect(progress).to_contain_text("1 / 13")
    expect(button(page, "保存进度")).to_have_text("已保存")
    page.locator(".wb-bar .wb-export").click()
    for label in ("下载草稿", "下载修改意见", "下载审阅记录", "下载进度文件"):
        expect(button(page, label)).to_be_visible()
    title = page.locator(".wb-doc-rename").inner_text().strip()
    folder = Path.home() / "Downloads"
    progress_path = folder / f"进度_{title}.json"
    export_file(page, pid, "下载进度文件", progress_path)
    exported = json.loads(progress_path.read_text(encoding="utf-8"))
    assert exported["review_meta"]["by_status"] == {"accepted": 1, "pending": 12}
    print("PASS: progress JSON written by the native Save As dialog", flush=True)

    names = export_names(title, "txt", draft=True)
    draft, record, feedback = (folder / names["final"], folder / names["record"], folder / f"{title}_修改意见.md")
    for label, file in (("下载草稿", draft), ("下载审阅记录", record), ("下载修改意见", feedback)):
        export_file(page, pid, label, file)
    original, revised = ((EXAMPLES / f"long_{side}.txt").read_text(encoding="utf-8")
                         for side in ("original", "revised"))
    assert draft.read_text(encoding="utf-8") == merge_decisions(exported, original, revised, pending="original").text
    assert "| 采用改句 | 1 |" in record.read_text(encoding="utf-8")
    assert title in feedback.read_text(encoding="utf-8")
    button(page, "完成").click()
    return progress_path


def upload_long_documents(page, root, *, revised_suffix=""):
    root.mkdir(exist_ok=True)
    for index, side in enumerate(("original", "revised")):
        document = root / f"{side} 中文.txt"
        text = (EXAMPLES / f"long_{side}.txt").read_text(encoding="utf-8")
        if side == "revised":
            text += revised_suffix
        document.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        page.locator(".wb-cols .wb-field").nth(index).locator('input[type="file"]').set_input_files(document)
        expect(page.get_by_text(document.name, exact=True)).to_be_visible()


def check_portable_progress(page, root, progress_path):
    button(page, "跳过").click()
    upload_long_documents(page, root)
    page.get_by_text("进度与恢复", exact=True).click()
    page.locator('.wb-import-options input[type="file"]').set_input_files(progress_path)
    button(page, "恢复审阅").click()
    expect(page.locator(".wb-prog")).to_contain_text("1 / 13")


def check_workbench(executable, profile, root, playwright):
    process, window, page, env = launch(executable, profile, playwright)
    try:
        subprocess.run([str(executable)], env=env, cwd=root, check=True, timeout=12)
        assert native_window(process.pid) == window
        check_review(page, root)
        close_app(process, window)
        print("PASS: standard user installation, single instance, tutorial, review, composition and native close",
              flush=True)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)

    process, window, page, _ = launch(executable, profile, playwright)
    try:
        button(page, "继续上次审阅").click()
        expect(page.locator(".wb-prog")).to_contain_text("2 / 13")
        expect(page.locator(".wb-fb .cm-content")).to_have_text("关闭前保存中文修改意见。")
        check_next_round(page)
        print("PASS: restored progress and notes, layout selection, npm CLI and next round", flush=True)
        expect(button(page, "保存进度")).to_have_text("已保存")
        close_app(process, window)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)

    process, window, page, _ = launch(executable, root / "导出验收", playwright)
    try:
        progress_path = check_native_export(page, process.pid)
        close_app(process, window)
        print("PASS: all four exports from the native Save As dialog", flush=True)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)

    process, window, page, _ = launch(executable, root / "导入验收", playwright)
    try:
        check_portable_progress(page, root, progress_path)
        close_app(process, window)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)

    print("PASS: portable progress reimported from UTF-8 files with BOM and Windows line endings", flush=True)


def uninstall(installed, executable, profile):
    saved = {file: file.read_bytes() for file in (profile / "storage").glob("storage-user-*.json")}
    uninstaller = next(installed.glob("unins*.exe"))
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   check=True, timeout=60)
    wait_for(lambda: not executable.exists(), "uninstallation")
    assert all(file.read_bytes() == content for file, content in saved.items()), "The uninstaller changed workbench data"
    print("PASS: uninstallation" + (" preserved workbench data" if saved else ""), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--node", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    if args.node:
        os.environ["PATH"] = os.pathsep.join((str(args.node.resolve(strict=True).parent), os.environ["PATH"]))
    expect.set_options(timeout=12000)

    setup = args.installer.resolve(strict=True)
    profile_dir = user_profile()
    local_data = profile_dir / "AppData/Local"
    roaming_data = profile_dir / "AppData/Roaming"
    temp = local_data / "Temp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ.update(USERPROFILE=str(profile_dir), LOCALAPPDATA=str(local_data), APPDATA=str(roaming_data),
                      TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    with TemporaryDirectory(prefix="验收台 Windows ", dir=temp, ignore_cleanup_errors=True) as directory, sync_playwright() as playwright:
        root = Path(directory)
        installed = root / "应用程序"
        profile = root / "验收专用数据"
        setup_test_model(root)
        setup_log = root / "setup.log"
        try:
            subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                            f"/DIR={installed}", f"/LOG={setup_log}"], check=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if setup_log.is_file():
                print(setup_log.read_text(encoding="utf-8-sig", errors="replace"), file=sys.stderr, flush=True)
            else:
                print(f"Setup did not create its log under {temp}.", file=sys.stderr, flush=True)
            raise
        executable = installed / "AcceptanceDesk.exe"
        try:
            check_workbench(executable, profile, root, playwright)
        finally:
            uninstall(installed, executable, profile)


if __name__ == "__main__":
    main()
