"""Cocoa window integration."""
import fcntl
import html
import os
from contextlib import contextmanager
from pathlib import Path

from desktop.launcher import BUNDLE_ID, NAME

GUI = "cocoa"
WEB_STORAGE = "WebKit"


def default_profile():
    return Path.home() / "Library" / "Application Support" / NAME


@contextmanager
def instance(profile):
    from AppKit import NSRunningApplication

    with (profile / "instance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID):
                if app.processIdentifier() != os.getpid():
                    app.activateWithOptions_(3)
                    break
            yield False
        else:
            yield True


def prepare():
    pass


def bind(window, profile):
    pass


def save_warning(window, message):
    from AppKit import NSAlert, NSApplication
    from PyObjCTools import AppHelper

    def show():
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        dialog = NSAlert.alloc().init()
        dialog.setMessageText_(NAME)
        dialog.setInformativeText_(message)
        dialog.runModal()

    AppHelper.callAfter(show)


def startup_error(error):
    import webview

    webview.create_window(NAME, html=(
        '<html lang="zh-CN"><meta charset="utf-8"><title>无法启动工作台</title>'
        '<body style="font:16px -apple-system;padding:24px"><h2>无法启动工作台</h2>'
        f'<p>{html.escape(str(error))}</p></body></html>'
    ), width=520, height=250)
    webview.start(gui=GUI)
