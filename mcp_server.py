"""本地 stdio MCP 薄壳。只接收文本并调用共享 audit()，不读写论文文件。"""

from __future__ import annotations

from typing import Any, Literal

from acceptance_desk import __version__, audit


def audit_texts(original: str, revised: str, mode: Literal["auto", "rules_only", "full"] = "auto") -> dict[str, Any]:
    """检查相邻两版中文 txt/md 文本，返回 schema v0.1 报告。

    每处修改仍待作者决定；flagged 是候选重点，不是已确认错误。
    模型只读本地权重；rules_only 不加载模型，auto 在权重不可用时退回规则。
    """
    return audit(original, revised, mode=mode)


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp_types import ToolAnnotations
    except ImportError as exc:
        raise RuntimeError("请安装 MCP 依赖：pip install 'acceptance-desk[mcp]'，或 uv sync --extra mcp") from exc
    server = MCPServer(
        "acceptance-desk", version=__version__,
        instructions="独立检查相邻两版中文稿。报告不代表作者决定，不自动接受修改；不评估 AIGC 率。",
    )
    server.tool(name="audit_texts", structured_output=True, annotations=ToolAnnotations(
        read_only_hint=True, destructive_hint=False, open_world_hint=False,
    ))(audit_texts)
    return server


def main() -> None:
    try:
        server = create_server()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
