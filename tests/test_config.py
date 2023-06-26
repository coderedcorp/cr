import unittest
from pathlib import Path
from pathlib import PurePosixPath

from cr.config import config
from cr.config import config_bool
from cr.config import config_path_list
from cr.config import config_pureposixpath_list
from cr.config import load_config


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
        # Check for and/or create config files.
        self.delete_home = False
        self.conf_home = Path("~/.cr.ini").expanduser().resolve()
        if not self.conf_home.exists():
            self.delete_home = True
            self.conf_home.touch()
        self.delete_cwd = False
        self.conf_cwd = Path(".cr.ini").resolve()
        if not self.conf_cwd.exists():
            self.delete_cwd = True
            self.conf_cwd.touch()
        # Write a custom config file.
        self.conf_custom = Path(".test-config.ini").resolve()
        with open(self.conf_custom, "w") as f:
            f.write(TEST_CONFIG)
        # Load the config.
        load_config([self.conf_custom])

    def tearDown(self):
        # Delete the config files.
        self.conf_custom.unlink()
        if self.delete_home:
            self.conf_home.unlink()
        if self.delete_cwd:
            self.conf_cwd.unlink()

    def test_loadconfig(self):
        paths = load_config([self.conf_custom])
        # Assert that the expected config files were loaded.
        self.assertTrue(str(self.conf_home) in paths)
        self.assertTrue(str(self.conf_cwd) in paths)
        self.assertTrue(str(self.conf_custom) in paths)

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
