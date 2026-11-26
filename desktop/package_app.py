"""Package a signed macOS application and verify the mounted distribution."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args, capture=False):
    return subprocess.run(args, text=True, check=True, capture_output=capture).stdout


def verify_app(app: Path) -> None:
    run("codesign", "--verify", "--deep", "--strict", app)


def package(app: Path, output: Path, *, identity: str | None, notary_profile: str | None) -> None:
    if output.exists():
        raise FileExistsError(f"目标已存在：{output}")
    verify_app(app)
    if identity:
        run("codesign", "--verify", "--test-requirement",
            "=anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists", app)

    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        work = Path(scratch)
        stage = work / "image"
        stage.mkdir()
        run("ditto", app, stage / app.name)
        (stage / "Applications").symlink_to("/Applications")
        label = "验收台" if identity else "验收台 · 本机预览"
        image = work / output.name
        run("hdiutil", "create", "-quiet", "-srcfolder", stage, "-volname", label, "-format", "UDZO", image)
        if identity:
            run("codesign", "--sign", identity, "--timestamp", image)
            result = json.loads(run("xcrun", "notarytool", "submit", image,
                                    "--keychain-profile", notary_profile, "--wait", "--output-format", "json",
                                    capture=True))
            if result["status"] != "Accepted":
                raise ValueError(f"Apple 公证未通过：{result['status']}，提交 ID {result['id']}")
            run("xcrun", "stapler", "staple", image)
            run("xcrun", "stapler", "validate", image)
            run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", image)

        mount = work / "mount"
        run("hdiutil", "attach", "-quiet", "-readonly", "-nobrowse", "-mountpoint", mount, image)
        try:
            mounted_app = mount / app.name
            verify_app(mounted_app)
            if identity:
                run("spctl", "--assess", "--type", "execute", mounted_app)
        finally:
            run("hdiutil", "detach", "-quiet", mount)
        os.link(image, output)

    print(f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local-preview", action="store_true")
    mode.add_argument("--identity", help="Developer ID Application signing identity")
    parser.add_argument("--notary-profile", help="notarytool keychain profile")
    args = parser.parse_args()
    if args.output.suffix != ".dmg":
        parser.error("输出文件必须使用 .dmg 扩展名。")
    if args.local_preview:
        if args.notary_profile or not args.output.name.endswith("-local-preview.dmg"):
            parser.error("本机预览使用 -local-preview.dmg 文件名，且无需公证配置。")
    elif not args.identity.startswith("Developer ID Application:") or not args.notary_profile:
        parser.error("正式包需要 Developer ID Application 签名身份和 --notary-profile。")
    try:
        package(args.app.resolve(), args.output.resolve(), identity=args.identity, notary_profile=args.notary_profile)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"打包失败：{exc}\n")


if __name__ == "__main__":
    main()
