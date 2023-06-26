import unittest
from pathlib import Path

from cr.utils import exec_proc
from cr.utils import get_command
from cr.utils import git_ignored


class TestSubprocesses(unittest.TestCase):
    def test_get_command(self):
        # Test that "python" resolves to a real file path.
        py = get_command("python")
        self.assertTrue(py.is_file())

        # Test that a full path to the program also resolves.
        py = get_command(py.resolve())
        self.assertTrue(py.is_file())

        # Test that a broken command raises an exception.
        with self.assertRaises(FileNotFoundError):
            get_command("!!! not a command !!!")

    def test_exec_proc(self):
        # A successful command.
        cmd = [
            "python",
            "-c",
            (
                "import sys\n"
                "print('this is stdout', file=sys.stdout)\n"
                "print('this is stderr', file=sys.stderr)\n"
                "sys.exit(0)\n"
            ),
        ]
        code, out, err = exec_proc(cmd)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "this is stdout")
        self.assertEqual(err.strip(), "this is stderr")

        # An erroneous command.
        cmd = [
            "python",
            "-c",
            (
                "import sys\n"
                "print('this is stdout', file=sys.stdout)\n"
                "print('this is stderr', file=sys.stderr)\n"
                "sys.exit(1)\n"
            ),
        ]
        code, out, err = exec_proc(cmd)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "this is stdout")
        self.assertEqual(err.strip(), "this is stderr")


class TestGit(unittest.TestCase):
    """
    It is assumed that git is installed on the machine running these tests.
    """

    def test_git_ignored(self):
        # Should contain some things that are being ignored in this repo.
        cwd = Path(__file__).parent
        lp = git_ignored(cwd)
        pycache = (cwd / "__pycache__").resolve()
        self.assertTrue(pycache in lp)

        # Should return an empty list because this is not a git repo.
        lp = git_ignored(Path("/").resolve())
        self.assertTrue(lp == [])
