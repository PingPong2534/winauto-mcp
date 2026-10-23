"""Guide storage invariants, using only disposable local data."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('app_guides', ROOT/'skills/winauto-mcp/scripts/app_guides.py')
guides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guides)


class AppGuideTests(unittest.TestCase):
    def test_latest_only_survives_reopen_and_rejects_stale_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            store = guides.GuideStore(folder)
            first = store.put('app', 'fixture-app', 'Fixture', 'Old observed recipe', 'missing', 'current', True)
            reopened = guides.GuideStore(folder)
            self.assertEqual(reopened.get('app', 'fixture-app')['revision'], first['revision'])
            (Path(folder)/'app/.write-interrupted').write_text('abandoned staging content')
            last = reopened.put('app', 'fixture-app', 'Fixture', 'Replacement verified recipe', first['revision'], 'current', True)
            with self.assertRaisesRegex(ValueError, 'Revision conflict'):
                store.put('app', 'fixture-app', 'Fixture', 'Stale overwrite', first['revision'], 'current', True)
            record = store.get('app', 'fixture-app')
            self.assertEqual(record['revision'], last['revision'])
            self.assertNotIn('Old observed', record['guide']['body'])
            self.assertEqual(len(list(Path(folder).rglob('*.json'))), 1)
            self.assertFalse(list(Path(folder).rglob('.write-*')))

    def test_invalidation_preserves_independent_facts_then_repair_replaces_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = guides.GuideStore(folder)
            first = store.put('web', 'https://EXAMPLE.com:443/', 'Fixture', 'Known structure. Obsolete recipe.', 'missing', 'current', True)
            invalid = store.put('web', 'https://example.com', 'Fixture', 'Known structure. Recipe unavailable: observed route changed.', first['revision'], 'needs_recheck', True)
            self.assertEqual(store.get('web', 'https://example.com')['guide']['status'], 'needs_recheck')
            self.assertNotIn('Obsolete recipe', store.get('web', 'https://example.com')['guide']['body'])
            store.put('web', 'https://example.com', 'Fixture', 'Known structure. Verified replacement.', invalid['revision'], 'current', True)
            self.assertEqual(len(store.list()), 1)
            self.assertEqual(store.list()[0]['status'], 'current')

    def test_review_size_and_origin_guards_do_not_write_rejected_data(self):
        with tempfile.TemporaryDirectory() as folder:
            store = guides.GuideStore(folder)
            for origin in ('https://user:password@example.com', 'https://example.com/?token=sample',
                           'https://example.com/#session', 'https://example.com/private/path', 'file:///private'):
                with self.assertRaises(ValueError):
                    store.put('web', origin, 'Fixture', 'text', 'missing', 'current', True)
            for body, reviewed in (('text', False), ('x'*12289, True), ('', True)):
                with self.assertRaises(ValueError):
                    store.put('app', 'fixture', 'Fixture', body, 'missing', 'current', reviewed)
            with self.assertRaises(ValueError):
                store.get('app', '../outside')
            self.assertFalse(list(Path(folder).rglob('*.json')))

    def test_parallel_writer_lock_refuses_without_replacing_current_record(self):
        with tempfile.TemporaryDirectory() as folder:
            store = guides.GuideStore(folder)
            first = store.put('app', 'fixture', 'Fixture', 'Verified', 'missing', 'current', True)
            path, _ = store.path('app', 'fixture')
            with guides.locked(path.parent/'.write.lock'):
                with self.assertRaises(OSError):
                    store.put('app', 'fixture', 'Fixture', 'Conflicting writer', first['revision'], 'current', True)
            self.assertEqual(store.get('app', 'fixture')['revision'], first['revision'])

    def test_linked_guide_records_cannot_read_or_replace_an_external_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            store = guides.GuideStore(base/'guides')
            path, _ = store.path('app', 'fixture')
            path.parent.mkdir(parents=True)
            outside = base/'outside.json'
            outside.write_text('private fixture data')
            try:
                path.symlink_to(outside)
            except OSError:
                self.skipTest('Creating a symlink is not permitted on this host')
            with self.assertRaises(ValueError):
                store.get('app', 'fixture')
            with self.assertRaises(ValueError):
                store.put('app', 'fixture', 'Fixture', 'replacement', 'missing', 'current', True)
            with self.assertRaises(ValueError):
                store.list()
            self.assertEqual(outside.read_text(), 'private fixture data')


if __name__ == '__main__':
    unittest.main(verbosity=2)
