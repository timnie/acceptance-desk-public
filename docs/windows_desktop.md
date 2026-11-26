# AI 改稿验收台 Windows 桌面版

Windows 安装程序面向 Windows 11 x64 和 Windows 10 22H2 x64。双击 `Acceptance-Desk-Windows-x64-Setup.exe` 安装，然后从开始菜单打开「AI 改稿验收台」。安装使用当前用户权限。系统缺少 WebView2 时，安装程序联网补装微软运行时。项目持续集成生成的预览安装包尚未使用发布证书签名。

Windows 和 macOS 桌面版共用工作台、文稿审阅、模型连接、轮次和导出代码。Windows 使用 Ctrl+S 保存、Ctrl+Z 撤销、Ctrl+Shift+Z 或 Ctrl+Y 重做。关闭窗口或按 Alt+F4 时会等待写入确认。工作台数据保存在 `%LOCALAPPDATA%\AI 改稿验收台`。重新安装和卸载都会保留该目录。如果要从 Mac 上迁移审阅决定，在 Mac 导出进度 JSON，再用 Windows 载入同一对文稿并恢复。完整进度 JSON 的范围沿用现有格式。详见[工作台手册](workbench.md)。

## 构建和验收

Windows 构建工作流使用锁定的 Python 依赖、PyInstaller 和 Inno Setup。在 GitHub 打开该提交的「Windows desktop」运行页。Windows 测试通过后可在页面底部的 Artifacts 下载预览安装程序。安装后的桌面验收结果见同一运行页。

验收脚本安装成品并启动原生 WebView2，检查首次教程、单实例、内置样稿、对校、审阅决定、快捷键、模拟中文组合输入、导出入口、退出保存、重开恢复及卸载保留数据。核对完整原生交互时，还要操作系统文件选择和保存对话框、真实中文输入法候选窗、系统外观切换、保存写入失败后的恢复，以及用户实际使用的 CLI 和 API 连接。使用虚构稿件和独立测试存储。工作台检查步骤见[手工验收表](workbench-testing.md)。
