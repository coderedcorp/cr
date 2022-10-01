from pathlib import Path, PurePosixPath
import unittest
import os

from cr.config import (
    load_config,
    config,
    config_bool,
    config_path_list,
    config_pureposixpath_list,
)


TEST_CONFIG = """
[cr]
token = cr_token
test_str = cr_str
test_paths =
    ./
    test_config.py
test_pureposixpaths =
    /dev/null/1
    # This is a multiline comment.
    /dev/null/2
test_bool_false = false
test_bool_true = true

[demo]
test_str = demo_str
"""


class TestCli(unittest.TestCase):
    def setUp(self):
        # Write a config file.
        self.custom_config = Path(".test-config.ini")
        with open(self.custom_config, "w") as f:
            f.write(TEST_CONFIG)
        # Load the config.
        load_config([self.custom_config])

    def tearDown(self):
        # Delete the config file.
        os.remove(self.custom_config)

    def test_loadconfig(self):
        paths = load_config([self.custom_config])
        # Assert that the expected config files were loaded.
        self.assertTrue(str(Path("~/.cr.ini").expanduser().resolve()) in paths)
        self.assertTrue(str(Path(".cr.ini").resolve()) in paths)
        self.assertTrue(str(self.custom_config) in paths)

    def test_config(self):
        self.assertEqual(config("test_str"), "cr_str")
        self.assertEqual(config("test_str", "demo"), "demo_str")
        # Test invalid webapp (should fallback to [cr])
        self.assertEqual(config("test_str", "junk"), "cr_str")
        # Test fallback.
        self.assertEqual(config("junk", f="junkval"), "junkval")

    def test_bool(self):
        self.assertTrue(config_bool("test_bool_true", "demo"))
        self.assertFalse(config_bool("test_bool_false", "demo"))
        # Test fallback.
        self.assertTrue(config_bool("junk", f=True))
        self.assertFalse(config_bool("junk", f=False))

    def test_path_list(self):
        paths = config_path_list("test_paths")
        self.assertEqual(
            paths,
            [Path(".").resolve(), Path("test_config.py").resolve()],
        )
        # Test fallback.
        paths = config_path_list("junk", f=[Path("/")])
        self.assertEqual(paths, [Path("/")])

    def test_pureposixpath_list(self):
        paths = config_pureposixpath_list("test_pureposixpaths")
        self.assertEqual(
            paths,
            [PurePosixPath("/dev/null/1"), PurePosixPath("/dev/null/2")],
        )
        # Test fallback.
        paths = config_pureposixpath_list("junk", f=[PurePosixPath("/")])
        self.assertEqual(paths, [PurePosixPath("/")])
