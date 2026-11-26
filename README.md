<h1 align="center">AI 改稿验收台</h1>

<p align="center">逐处审阅 AI 对中文文稿的改动，保留原文和每一次取舍。</p>

<p align="center">
  <strong><a href="#安装">安装</a></strong>
  · <a href="#快速上手">快速上手</a>
  · <a href="#从源码运行">从源码运行</a>
  · <a href="docs/workbench.md">工作台手册</a>
  · <a href="https://github.com/timnie/acceptance-desk-public/releases">发行说明</a>
  · <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="docs/workbench.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/wb_frontend_20261115_dark.png">
      <img src="docs/screenshots/wb_frontend_20261115_light.png" width="100%" alt="工作台中的文稿正文和审阅控件">
    </picture>
  </a>
</p>

## 主要功能

- **原稿和改稿并排看。** 在段落上下文中核对增删和改写，检查数字、引文和措辞的变化。
- **逐处作决定。** 采用改句、保留原句或自行编辑。可撤销、记录意见、保存进度。
- **把意见带到下一轮。** 导出定稿、审阅记录和修改意见。桌面预览版也可以在预览发送内容后，将文稿和意见交给你选择的模型。

## 安装

### macOS · Apple 芯片

使用 [Homebrew](https://brew.sh/) 安装。

```bash
brew tap timnie/acceptance-desk-public https://github.com/timnie/acceptance-desk-public.git
brew install --cask timnie/acceptance-desk-public/acceptance-desk
```

也可[直接下载构建 10（ZIP）](https://github.com/timnie/acceptance-desk-public/releases/download/macos-beta-build-10-zip/Acceptance-Desk-macOS-arm64-Build-10.zip)，解压后把 App 拖入「应用程序」。预览版尚未公证，首次打开可能需要在[「隐私与安全性」](https://support.apple.com/zh-cn/102445)中单独确认。

### Windows · x64

使用 [Scoop](https://scoop.sh/) 安装。

```powershell
scoop bucket add acceptance-desk https://github.com/timnie/acceptance-desk-public.git
scoop install acceptance-desk/acceptance-desk
```

也可[直接下载预览安装器（EXE）](https://github.com/timnie/acceptance-desk-public/releases/download/windows-beta-20261125-layout-fix/Acceptance-Desk-Windows-x64-Setup-2026-11-25-layout-fix.exe)。当前 Windows 预览安装器未经签名。

### Linux

[从源码启动浏览器工作台](#从源码运行)，或使用[命令行](#命令行)。

## 快速上手

1. 打开验收台，选择内置示例，或粘贴、导入同一篇文稿的两版 UTF-8 文本（TXT 或 Markdown）。
2. 打开「并排对校」，找到一处有意义的变化，选择「采用改稿」或「保留原稿」，需要时写下意见。
3. 预览合并结果，再导出定稿和审阅记录。也可以先保存进度，下次继续。

审阅和导出在本机完成。使用桌面版「生成新稿」时，只有确认发送后，文稿和修改意见才会交给所选模型连接。

### 看一处实际改动

| 原稿 | AI 改稿 |
| --- | --- |
| 问卷结果**可能**说明，线上讨论频率与期末成绩**相关**。 | 问卷结果说明，线上讨论频率**直接导致**了期末成绩的提升。 |

「可能」消失了，「相关」变成了因果判断。打开[这对虚构样稿](examples/)，可以连同前后段一起审阅，再决定保留哪一句。

## 从源码运行

安装 [uv](https://docs.astral.sh/uv/)，在仓库根目录运行以下命令，然后访问 `http://127.0.0.1:8780`。

```bash
uv sync --locked --extra web
uv run --locked --extra web python -m workbench
```

### 命令行

检查一对示例文稿，输出修改摘要。

```bash
uv run --locked acceptance-desk examples/original.txt examples/revised.txt --brief
```

## 反馈与开发

在 [GitHub Issues](https://github.com/timnie/acceptance-desk-public/issues) 反馈问题时，请附上平台、构建版本和用虚构文稿复现的步骤。贡献代码请见[贡献指南](CONTRIBUTING.md)。

[报告结构](docs/schema.md) · [导出格式](docs/export.md) · [本地 MCP 接入](docs/mcp.md) · [持续集成与打包](docs/ci.md) · [文档索引](docs/README.md)

运行 `uv run --locked --group dev --extra web pytest -q` 执行开发测试。CI 覆盖 Python 3.10 和 3.12。可选的离线语义功能见[模型配置](experiments/step1_models.md)。

## 许可证

项目源码采用 [MIT 许可证](LICENSE)。
