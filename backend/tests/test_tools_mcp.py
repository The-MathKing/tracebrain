import json

from fixtures_b import R, fk, graph, tenant  # noqa: F401
from tracebrain import mcp_server
from tracebrain.tools.registry import TOOLS


async def test_mcp_exposes_registry_tools(tenant, monkeypatch):
    monkeypatch.setattr(mcp_server, "TENANT", tenant)
    tools = await mcp_server.server.list_tools()
    assert sorted(t.name for t in tools) == sorted(t.name for t in TOOLS)
    recall = next(t for t in tools if t.name == "recall")
    assert "user_id" in recall.input_schema["properties"]
    res = await mcp_server.server.call_tool("blast_radius", {"key": fk("core/vector_index.py")})
    data = json.loads(res.content[0].text)
    assert data["data"]["affected_count"] == 3 and data["trace"]["nodes"]
    await mcp_server.server.call_tool("remember", {"text": "Uses MCP from Claude Code", "kind": "procedural",
                                                   "user_id": "mcpuser"})
    res = await mcp_server.server.call_tool("recall", {"query": "MCP Claude", "user_id": "mcpuser"})
    assert json.loads(res.content[0].text)["data"]["facts"][0]["text"] == "Uses MCP from Claude Code"
