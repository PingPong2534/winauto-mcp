"""Missing optional Unity must not launch apps or hide real probe failures."""
import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import probe_app_interop as interop


class AppProbeOptionsTests(unittest.TestCase):
    def args(self, output):
        return argparse.Namespace(output=output, unity='missing/Unity.exe', edge=None,
                                  reuse_unity=False, editor_only=False)

    def run_probe(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(interop.main(args))
        return json.loads((args.output / 'evidence.json').read_text())

    def test_missing_editor_skips_without_starting_stdio_or_unity(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(interop, 'unity_executable', return_value=None), \
             patch.object(interop, 'stdio_client') as stdio, \
             patch.object(interop.Probe, 'unity', new_callable=AsyncMock) as unity:
            result = self.run_probe(self.args(Path(folder) / 'proof'))
            self.assertEqual(result['status'], 'skipped')
            self.assertIsNone(result['passed'])
            self.assertEqual(result['completed'], [])
            self.assertEqual(result['skipped'], ['unity'])
            self.assertEqual(result['unity-probe']['reason'], 'Unity Editor executable not found')
            self.assertFalse((Path(folder) / 'proof/unity-project').exists())
            stdio.assert_not_called()
            unity.assert_not_awaited()

    def test_missing_editor_only_skips_without_reusing_a_player(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(interop, 'unity_executable', return_value=None), \
             patch.object(interop, 'stdio_client') as stdio:
            args = self.args(Path(folder) / 'proof')
            args.editor_only = args.reuse_unity = True
            self.assertEqual(self.run_probe(args)['skipped'], ['unity'])
            stdio.assert_not_called()

    def test_local_editor_path_and_path_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            editor = Path(folder) / 'Unity.exe'
            editor.touch()
            self.assertEqual(interop.unity_executable(str(editor)), str(editor.resolve()))
            self.assertIsNone(interop.unity_executable(str(editor / 'missing')))
        with patch.object(interop.shutil, 'which', return_value='C:/Editor/Unity.exe'):
            self.assertEqual(interop.unity_executable('Unity.exe'), 'C:/Editor/Unity.exe')
        self.assertIsNone(interop.unity_executable(None))

    def session(self):
        transport = MagicMock()
        transport.__aenter__.return_value = (MagicMock(), MagicMock())
        client = MagicMock()
        client.__aenter__.return_value.initialize = AsyncMock()
        return transport, client

    def test_edge_continues_when_unity_is_missing(self):
        transport, client = self.session()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(interop, 'unity_executable', return_value=None), \
             patch.object(interop, 'stdio_client', return_value=transport), \
             patch.object(interop, 'ClientSession', return_value=client), \
             patch.object(interop.Probe, 'edge', new_callable=AsyncMock) as edge, \
             patch.object(interop.Probe, 'unity', new_callable=AsyncMock) as unity:
            args = self.args(Path(folder) / 'proof')
            args.edge = 'msedge.exe'
            result = self.run_probe(args)
            edge.assert_awaited_once_with('msedge.exe')
            unity.assert_not_awaited()
            self.assertEqual(result['completed'], ['edge'])
            self.assertEqual(result['skipped'], ['unity'])
            self.assertTrue(result['passed'])

    def test_installed_unity_failure_is_not_converted_to_skip(self):
        transport, client = self.session()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(interop, 'unity_executable', return_value='C:/Editor/Unity.exe'), \
             patch.object(interop, 'stdio_client', return_value=transport), \
             patch.object(interop, 'ClientSession', return_value=client), \
             patch.object(interop.Probe, 'unity', new_callable=AsyncMock,
                          side_effect=RuntimeError('fixture build failed')) as unity:
            args = self.args(Path(folder) / 'proof')
            with self.assertRaisesRegex(RuntimeError, 'fixture build failed'):
                self.run_probe(args)
            unity.assert_awaited_once_with('C:/Editor/Unity.exe', False)
            self.assertFalse((args.output / 'evidence.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
