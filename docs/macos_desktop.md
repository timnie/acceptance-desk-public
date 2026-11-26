# macOS 桌面版

验收台支持 Apple 芯片 Mac。下载 ZIP 后，解压并将「AI 改稿验收台.app」移到「应用程序」。未公证的预览版在首次打开时可能要求在「系统设置 → 隐私与安全性」中单独确认。仅在发布来源可信且校验值一致时确认。 [Apple 说明](https://support.apple.com/zh-cn/102445)

文稿、编辑草稿和会话保存在 `~/Library/Application Support/AI 改稿验收台/`。首次使用可跟随导入页的教程。关闭窗口前，应用会等待保存审阅进度。

## 构建与打包

在 Apple 芯片 Mac 的仓库根目录运行。

```sh
UV_PROJECT_ENVIRONMENT=build/desktop/venv312 uv sync --locked --python 3.12 --no-dev --no-install-project --extra web --group desktop
build/desktop/venv312/bin/python -m PyInstaller --noconfirm --distpath dist --workpath build/desktop/frozen desktop/workbench.spec
codesign --verify --deep --strict 'dist/AI 改稿验收台.app'
ditto -c -k --keepParent 'dist/AI 改稿验收台.app' dist/Acceptance-Desk-macOS-arm64.zip
shasum -a 256 dist/Acceptance-Desk-macOS-arm64.zip
```

验证发布包时，用上面生成的 ZIP 解压到一个新目录，再检查签名并打开内置示例。用 `ACCEPTANCE_DESK_DESKTOP_PROFILE` 可为命令行启动使用单独的测试存档。Homebrew 与手动安装使用同一份 ZIP。
