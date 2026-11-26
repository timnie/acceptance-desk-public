"""真实 stdio 子进程往返，包括当前协议与旧客户端握手；仅用虚构文本。"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="需要可选 mcp 依赖")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
def test_stdio_report_and_invalid_input(protocol, tmp_path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from acceptance_desk import validate_report

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                       cwd=tmp_path, env={"HF_HUB_OFFLINE": "1"})
        async with Client(params, mode=protocol, read_timeout_seconds=15) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools.tools] == ["audit_texts"]
            original, revised = "结果可能有效。", "结果有效。"
            result = await client.call_tool("audit_texts", {"original": original, "revised": revised,
                                                          "mode": "rules_only"})
            assert not result.is_error
            report = result.structured_content
            assert not validate_report(report, original, revised)
            assert report["summary"]["auto_passed"] == 0
            assert report["run"]["classifier"]["status"] == "untrained"
            invalid = await client.call_tool("audit_texts", {"original": original, "revised": revised,
                                                           "mode": "unknown"})
            assert invalid.is_error
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run())
