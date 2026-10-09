"""Exercise native installation only in disposable client homes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('winauto_install', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def test_move_changes_connector_cache_and_foreign_marketplace_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            original = base / 'original'
            original.mkdir()
            shutil.copytree(ROOT / 'skills', original / 'skills')
            shutil.copy2(ROOT / 'installation.json', original / 'installation.json')
            (original / 'requirements.txt').write_text('')
            relocated = base / 'relocated'
            shutil.copytree(original, relocated)
            plan = installer.load_plan(original / 'installation.json')
            _, first = installer.build_bundle(original, base / 'state', plan, 'python.exe')
            _, moved = installer.build_bundle(relocated, base / 'state', plan, 'python.exe')
            self.assertNotEqual(first, moved)
            self.assertEqual(installer.read_json(moved / '.mcp.json')['mcpServers']['winauto']['args'],
                             [str(relocated / 'server.py')])
            foreign = [{'name': plan['marketplace'], 'source': 'directory', 'path': str(original)}]
            with mock.patch.object(installer, 'command', return_value=foreign) as command:
                with self.assertRaisesRegex(RuntimeError, 'different source'):
                    installer.ensure_marketplace('claude-code', 'test-client', str(relocated),
                                                 plan['marketplace'], {})
            self.assertEqual(command.call_count, 1)  # read only; no mutation of the foreign source

    def test_failed_probe_clears_a_previous_success_report(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installer.write_json(base / 'state/installation-result.json', {'ok': True})
            with mock.patch.object(installer, 'executable', return_value='test-client'), \
                    mock.patch.object(installer, 'command', return_value=''), \
                    mock.patch.object(installer, 'probe_server', side_effect=RuntimeError('server unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'server unavailable'):
                    installer.main(['--client', 'codex', '--state-dir', str(base / 'state'),
                                    '--codex-home', str(base / 'codex')])
            report = installer.read_json(base / 'state/installation-result.json')
            self.assertFalse(report['ok'])
            self.assertEqual(report['phase'], 'preflight')
            self.assertFalse(report['clients'])

    def test_required_plugin_failure_stops_main_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            plan = installer.load_plan(ROOT / 'installation.json')
            plan['required_plugins']['codex'] = [{
                'id': 'required@test-market', 'marketplace_source': 'test/repo'}]
            with mock.patch.object(installer, 'load_plan', return_value=plan), \
                    mock.patch.object(installer, 'executable', return_value='test-client'), \
                    mock.patch.object(installer, 'command', return_value=''), \
                    mock.patch.object(installer, 'probe_server', return_value=sorted(installer.REQUIRED_TOOLS)), \
                    mock.patch.object(installer, 'ensure_marketplace'), \
                    mock.patch.object(installer, 'ensure_plugin', side_effect=RuntimeError('missing dependency')) as plugin:
                with self.assertRaisesRegex(RuntimeError, 'missing dependency'):
                    installer.main(['--client', 'codex', '--state-dir', str(base / 'state'),
                                    '--codex-home', str(base / 'codex')])
            self.assertEqual(plugin.call_count, 1)
            self.assertEqual(plugin.call_args.args[2], 'required@test-market')
            report = installer.read_json(base / 'state/installation-result.json')
            self.assertFalse(report['ok'])
            self.assertFalse(report['clients'])
            self.assertIn('missing dependency', report['error'])

    def test_missing_skill_does_not_publish_a_partial_plugin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'skill is missing'):
                installer.build_bundle(root / 'source', root / 'state',
                                       installer.load_plan(ROOT / 'installation.json'), 'python.exe')
            self.assertFalse((root / 'state').exists())

    def test_stale_skill_and_wrong_connector_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'source', Path(folder) / 'target'
            (source / 'skills/test').mkdir(parents=True)
            (source / 'skills/test/SKILL.md').write_text('required workflow')
            shutil.copytree(source, target)
            installer.write_json(source / '.mcp.json', {'mcpServers': {'winauto': {'command': 'right'}}})
            installer.write_json(target / '.mcp.json', {'mcpServers': {'winauto': {'command': 'wrong'}}})
            with self.assertRaisesRegex(RuntimeError, 'connector'):
                installer.verify_bundle(source, target)
            (target / 'skills/test/SKILL.md').write_text('old workflow')
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                installer.verify_bundle(source, target)

    @unittest.skipUnless(os.name == 'nt' and installer.executable('codex')
                         and installer.executable('claude-code'), 'Both native client CLIs required')
    def test_native_install_dependency_update_and_preserved_settings(self):
        with tempfile.TemporaryDirectory(prefix='winauto install ทดสอบ ') as folder:
            base = Path(folder)
            repo = base / 'checkout'
            repo.mkdir()
            for file in [*ROOT.glob('*.py'), ROOT / 'requirements.txt', ROOT / 'installation.json']:
                shutil.copy2(file, repo / file.name)
            shutil.copytree(ROOT / 'skills', repo / 'skills')
            helper = base / 'dependency/plugin'
            meta = {'name': 'winauto-test-helper', 'version': '1.0.0',
                    'description': 'Disposable installation prerequisite',
                    'author': {'name': 'winauto tests'}}
            installer.write_json(helper / '.claude-plugin/plugin.json', meta)
            installer.write_json(helper / '.codex-plugin/plugin.json', dict(
                meta, skills='./skills/', interface={'displayName': 'Install test helper',
                                                      'shortDescription': 'Disposable prerequisite plugin'}))
            skill = helper / 'skills/helper/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('---\nname: helper\ndescription: Disposable install-test helper.\n---\nRead the requested test fixture.\n')
            installer.write_json(base / 'dependency/.agents/plugins/marketplace.json', {
                'name': 'winauto-test-deps', 'plugins': [{'name': meta['name'],
                    'source': {'source': 'local', 'path': './plugin'},
                    'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
                    'category': 'Productivity'}]})
            installer.write_json(base / 'dependency/.claude-plugin/marketplace.json', {
                'name': 'winauto-test-deps', 'owner': meta['author'],
                'plugins': [{'name': meta['name'], 'source': './plugin'}]})
            plan = installer.load_plan(repo / 'installation.json')
            dependency = {'id': 'winauto-test-helper@winauto-test-deps',
                          'marketplace_source': str(base / 'dependency')}
            for client in installer.CLIENTS:
                plan['required_plugins'][client] = [dependency]
            installer.write_json(repo / 'installation.json', plan)
            codex, claude = base / 'codex', base / 'claude'
            codex.mkdir()
            (codex / 'config.toml').write_text('[mcp_servers.unrelated]\ncommand = "preserve.exe"\n')
            installer.write_json(claude / 'settings.json', {'env': {'WINAUTO_TEST': 'preserve'}})
            args = ['--client', 'both', '--state-dir', str(base / 'state'),
                    '--codex-home', str(codex), '--claude-home', str(claude)]
            with mock.patch.object(installer, 'ROOT', repo), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(installer.main(args), 0)
                first = installer.read_json(base / 'state/installation-result.json')
                self.assertTrue(first['ok'])
                for client in installer.CLIENTS:
                    self.assertEqual(first['clients'][client]['tool_count'], 35)
                    self.assertEqual(first['clients'][client]['required_plugins'], [dependency['id']])
                # Source change must reach both native caches on reinstall.
                ref = repo / 'skills/winauto-mcp/references/tools.md'
                ref.write_text(ref.read_text(encoding='utf-8') + '\nUpdated test reference.\n', encoding='utf-8')
                self.assertEqual(installer.main(args), 0)
                updated = installer.read_json(base / 'state/installation-result.json')
            env = os.environ.copy()
            env.update(CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(claude))
            for client in installer.CLIENTS:
                old = first['clients'][client]['installed_path']
                new = updated['clients'][client]['installed_path']
                self.assertNotEqual(old, new)
                self.assertEqual((Path(new) / 'skills/winauto-mcp/references/tools.md').read_bytes(), ref.read_bytes())
                entries = installer.installed(client, installer.executable(client), env)
                key = 'pluginId' if client == 'codex' else 'id'
                for plugin_id in (dependency['id'], 'winauto-mcp@winauto-local'):
                    matches = [entry for entry in entries if entry[key] == plugin_id]
                    self.assertEqual(len(matches), 1)
                    self.assertTrue(matches[0]['enabled'])
            self.assertEqual(installer.read_json(claude / 'settings.json')['env']['WINAUTO_TEST'], 'preserve')
            untouched = installer.command([installer.executable('codex'), 'mcp', 'get',
                                            'unrelated', '--json'], env, True)
            self.assertEqual(untouched['transport']['command'], 'preserve.exe')
            self.assertTrue(list((base / 'state/backups').rglob('config.toml')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
