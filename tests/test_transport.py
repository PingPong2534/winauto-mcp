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
                    self.assertEqual(len(names), 36)
                    self.assertNotIn("wait_stable", names)
                    self.assertTrue({"read_element", "set_value", "run_steps", "control_set", "foreground_steps", "guarded_steps"} <= names)
                    guarded = next(tool for tool in listing.tools if tool.name == "guarded_steps")
                    self.assertEqual(guarded.input_schema['properties']['vision_backend']['default'], 'auto')
                    self.assertFalse(guarded.input_schema['properties']['capture_final']['default'])
                    control = next(tool for tool in listing.tools if tool.name == "control_set")
                    self.assertEqual(control.input_schema["properties"]["steps"]["type"], "array")
                    attach = next(tool for tool in listing.tools if tool.name == "attach_window")
                    self.assertEqual(attach.input_schema["properties"]["label"]["type"], "string")
                    drag = next(tool for tool in listing.tools if tool.name == "drag")
                    self.assertFalse(drag.input_schema['properties']['allow_takeover']['default'])
                    self.assertEqual(drag.input_schema['properties']['duration_ms']['default'], 500)
                    self.assertEqual(drag.input_schema['properties']['reason']['type'], 'string')
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
                    result = await session.call_tool("control_set", {
                        "automation_id": "101", "steps": [{"do": "capture"}],
                    })
                    self.assertTrue(result.is_error)
                    self.assertIn("no window attached", next(b.text for b in result.content if b.type == "text"))

        with tempfile.TemporaryDirectory(prefix="winauto-transport-") as cwd:
            asyncio.run(scenario(cwd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
