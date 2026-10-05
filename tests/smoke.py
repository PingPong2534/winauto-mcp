"""Run current regression tests; never launch Notepad or a user's app."""
import pathlib
import unittest

if __name__ == "__main__":
    tests = pathlib.Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(tests), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
