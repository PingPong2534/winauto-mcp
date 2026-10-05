"""Verify the actual stdio server from a working directory outside the repo."""
import asyncio
import json
import pathlib
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class TransportTests(unittest.TestCase):
    def test_stdio_handshake_and_background_tools(self):
        root = pathlib.Path(__file__).resolve().parents[1]

        async def scenario(cwd):
            params = StdioServerParameters(
                command=str(root / ".venv" / "Scripts" / "python.exe"),
                args=[str(root / "server.py")], cwd=cwd,
                env={"TEMP": cwd, "TMP": cwd},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=20) as session:
                    info = await session.initialize()
                    self.assertEqual(info.server_info.name, "winauto-mcp")
                    listing = await session.list_tools()
                    names = {tool.name for tool in listing.tools}
                    self.assertEqual(len(names), 34)
                    self.assertTrue({"read_element", "set_value", "run_steps"} <= names)
                    result = await session.call_tool("keyboard_status", {})
                    self.assertFalse(result.is_error)
                    state = json.loads(next(b.text for b in result.content if b.type == "text"))
                    self.assertEqual(state["backend"], "pywinauto-uia")
                    self.assertFalse(state["blocking"])
                    self.assertFalse(state["watching"])
                    result = await session.call_tool("type_text", {"text": "must not type"})
                    self.assertTrue(result.is_error)
                    message = next(b.text for b in result.content if b.type == "text")
                    self.assertIn("no window attached", message)

        with tempfile.TemporaryDirectory(prefix="winauto-transport-") as cwd:
            asyncio.run(scenario(cwd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
