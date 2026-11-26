# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from desktop.launcher import BUNDLE_ID, NAME

macos = sys.platform == "darwin"
renderer_imports = ["webview.platforms.cocoa"] if macos else [
    "webview.platforms.winforms", "webview.platforms.edgechromium",
]
data = [
    (str(root / "workbench/style.css"), "workbench"),
    (str(root / "workbench/assets"), "workbench/assets"),
    (str(root / "acceptance_desk/resources/lexicon.toml"), "acceptance_desk/resources"),
    (str(root / "acceptance_desk/resources/scope_review.md"), "acceptance_desk/resources"),
]
for filename in ("original.txt", "revised.txt", "long_original.txt", "long_revised.txt"):
    data.append((str(root / "examples" / filename), "workbench/examples"))
for package in ("nicegui", "pywebview"):
    data.extend(copy_metadata(package))

a = Analysis([str(root / "desktop/entry.py")], pathex=[str(root)], datas=data,
             hiddenimports=[*renderer_imports, "engineio.async_drivers.asgi",
                            "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
                            "uvicorn.protocols.websockets.wsproto_impl", "uvicorn.lifespan.on"],
             excludes=["tkinter", "pytest", "IPython", "numpy", "torch", "transformers"])
# NiceGUI registers ESM directories eagerly, even for elements the workbench never renders.
unused_assets = tuple(f"nicegui/elements/{element}/dist/" for element in
                      ("plotly", "mermaid", "echart", "aggrid", "json_editor", "scene"))
directory_marker = Path(CONF["workpath"]) / "not-bundled.txt"
directory_marker.write_text("This element is not used by the workbench.\n", encoding="utf-8")
a.datas = [entry for entry in a.datas if not entry[0].replace("\\", "/").startswith(unused_assets)]
a.datas += [(prefix + directory_marker.name, str(directory_marker), "DATA") for prefix in unused_assets]

pyz = PYZ(a.pure)
mac_options = {"target_arch": "arm64",
               "codesign_identity": os.environ.get("ACCEPTANCE_DESK_SIGN_IDENTITY")} if macos else {}
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AcceptanceDesk", console=False, **mac_options)
files = COLLECT(exe, a.binaries, a.datas, name="AcceptanceDesk")
if macos:
    app = BUNDLE(files, name=NAME + ".app", bundle_identifier=BUNDLE_ID,
                 info_plist={"CFBundleDisplayName": NAME, "CFBundleName": NAME,
                             "LSMultipleInstancesProhibited": True,
                             "NSHighResolutionCapable": True,
                             "CFBundleShortVersionString": "0.0.1", "CFBundleVersion": "10"})
