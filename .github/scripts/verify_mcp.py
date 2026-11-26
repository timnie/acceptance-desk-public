"""从源码目录之外启动已安装的 MCP 入口，用虚构文本验证两种协议。"""

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from acceptance_desk import validate_report


async def verify():
    command = str(Path(sys.executable).parent / "acceptance-desk-mcp")
    with tempfile.TemporaryDirectory(prefix="acceptance-mcp-wheel-") as tmp:
        for mode in ("auto", "legacy"):
            async with Client(StdioServerParameters(command=command, cwd=tmp, env={"HF_HUB_OFFLINE": "1"}),
                              mode=mode, read_timeout_seconds=15) as client:
                tools = await client.list_tools()
                assert [t.name for t in tools.tools] == ["audit_texts"]
                original, revised = "结果可能成立。", "结果成立。"
                result = await client.call_tool("audit_texts", {"original": original, "revised": revised,
                                                              "mode": "rules_only"})
                assert not result.is_error
                assert not validate_report(result.structured_content, original, revised)
                assert result.structured_content["summary"]["auto_passed"] == 0
        assert not list(Path(tmp).iterdir())
    print("Installed MCP stdio verified: current and legacy protocols")


if __name__ == "__main__":
    asyncio.run(verify())
