import unittest

from cr.cli import runcli


class TestCli(unittest.TestCase):
    def test_runcli(self):
        # Simply test that a default runcli() doesn't blow up.
        try:
            runcli()
            self.assertTrue(True)
        except Exception:
            self.assertTrue(False)
