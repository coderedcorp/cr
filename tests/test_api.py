import unittest

from cr.api import check_update


class TestApi(unittest.TestCase):
    def test_check_update(self):
        # Test that check_update works, and returns a value.
        has_update, version = check_update()
        self.assertIsInstance(has_update, bool)
        # If version is None, that means something failed.
        self.assertIsNotNone(version)
