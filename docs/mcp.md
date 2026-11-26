# 本地 MCP 与插件准备

`mcp_server.py` 的本地 stdio 工具 `audit_texts(original, revised, mode)` 接收两版文本，直接调用共享的 `audit()` 并返回 schema v0.1 报告。审计在本机执行，各条修改均待作者决定。

## 维护者启动

```bash
uv sync --locked --extra mcp
uv run --locked --no-dev --extra mcp acceptance-desk-mcp
```

启动后等待标准输入上的 MCP 请求，终端没有网页是正常行为。可选模式为 `auto`、`rules_only`、`full`；模型路径通过 `ACCEPTANCE_DESK_MODEL_CACHE` 指定，只读本地权重。服务仅实现 stdio，不开放 HTTP 端口。

已有可选 MCP SDK 依赖现明确为 `mcp>=2.3,<3`（锁文件为 2.3.0），使用 `MCPServer`，并以当前协议和旧版握手分别测试。SDK 的 [stdio 运行说明](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md) 与 [迁移说明](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md) 是接口依据。

## 插件文件

- `.claude-plugin/plugin.json`：插件元数据。
- `.claude-plugin/marketplace.json`：本仓库作为私有团队目录的条目。
- `.mcp.json`：使用 `uv run --directory ${CLAUDE_PLUGIN_ROOT} --locked --no-dev --extra mcp acceptance-desk-mcp`；不依赖用户当前工作目录。
- `skills/acceptance-review/SKILL.md` 说明怎样使用本地审计依据并收集作者决定。

插件路径与清单依据 [Claude Code manifest 参考](https://code.claude.com/docs/en/plugins-reference) 和 [marketplace 说明](https://code.claude.com/docs/en/plugin-marketplaces)。私有目录可供有仓库访问权限的成员使用，不要求先把论文仓库公开。没有向官方市场提交。

## 验收边界

自动测试已实际启动 stdio 子进程，发现工具、调用公开虚构文本、校验结构化报告，并检查错误模式被拒绝；当前协议与旧版握手都覆盖。CI 安装 MCP 依赖，避免测试被静默跳过。安装包也包含工作台、CSS、共用状态模块和 MCP 入口。

这些检查不等于两款真实 agent 的安装验收。本机未找到 Claude Code CLI，未修改用户现有 agent 配置。需要在 Claude Code 和 Codex 中分别注册上述本地启动命令，用同一组虚构文本实际调用一次并保存工具名、模式和结果；不要只以“配置文件存在”作为通过。公开发布、许可证选择和线上论文存储仍未决定。源码先前写有 MIT 元数据，但无 LICENSE 且发布记录明确待定，本轮去掉该未经确认的元数据，未替团队选许可证。
