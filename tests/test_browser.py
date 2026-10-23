"""Validation and isolation invariants; no user browser or account is touched."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_backend import BrowserWorker, ProfileLease, checked_url, display_url, validate_steps


class BrowserValidationTests(unittest.TestCase):
    def test_no_script_urls_credentials_or_profile_traversal(self):
        for url in ['javascript:alert(1)', 'file:///C:/secret', 'https://user:password@example.com']:
            with self.assertRaises(ValueError): checked_url(url)
        self.assertEqual(display_url('https://example.com/login?token=private#secret'), 'https://example.com/login')
        self.assertEqual(display_url('https://fixture-user:fixture-password@example.com:8443/login?token=private#secret'), 'https://example.com:8443/login')
        self.assertEqual(display_url('https://user:pass@[::1]:8443/path'), 'https://[::1]:8443/path')
        worker = BrowserWorker()
        for profile in ['../personal', 'C:\\personal', 'CON', '', 'a'*49]:
            with self.assertRaises(RuntimeError): worker.call('open', profile=profile, url='about:blank', browser='msedge')
        self.assertEqual(worker.call('status')['sessions'], [])

    def test_complete_syntax_and_file_validation_precedes_actions(self):
        for steps in [[{'do':'evaluate', 'ref':'e1'}], [{'do':'fill','ref':'e1'}],
                      [{'do':'click','ref':'e1','force':True}], [{'do':'upload','ref':'e1','files':['missing.mp4']}]]:
            with self.assertRaises(ValueError): validate_steps(steps)
        with tempfile.TemporaryDirectory() as root:
            file = Path(root)/'test.txt';file.write_text('owned fixture')
            validate_steps([{'do':'upload','ref':'e1','files':[str(file)]}])

    @unittest.skipUnless(os.name == 'nt', 'Windows profile lease')
    def test_profile_has_exclusive_os_released_lease(self):
        with tempfile.TemporaryDirectory() as root:
            first = ProfileLease(Path(root))
            try:
                with self.assertRaises(ValueError): ProfileLease(Path(root))
            finally: first.close()
            second = ProfileLease(Path(root)); second.close()

    def test_busy_worker_refuses_without_queueing_a_second_action(self):
        worker = BrowserWorker(); entered = threading.Event(); finish = threading.Event()
        worker._slow = lambda: (entered.set(), finish.wait(5))
        task = threading.Thread(target=lambda: worker.call('slow'));task.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(RuntimeError, 'busy'): worker.call('status')
            self.assertTrue(worker._requests.empty())
        finally: finish.set();task.join(3)


if __name__ == '__main__': unittest.main(verbosity=2)
