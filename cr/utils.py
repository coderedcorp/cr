"""
Subprocess and filesystem utilities for cross-platform compatibility.

Copyright (c) 2022 CodeRed LLC.
"""
from pathlib import Path
from subprocess import PIPE, Popen
from typing import IO, List, Tuple, Union
import io
import os

from cr import LOGGER


EXCLUDE_DIRNAMES = ["__pycache__", "node_modules", "htmlcov", "venv"]
"""
List of directory names to always exclude from deployments.
"""


def get_command(program: str) -> Path:
    r"""
    Finds full path to a command on PATH given a command name.
    E.g. ``bash`` returns ``/bin/bash``;
    ``git`` might return ``C:\Program Files\Git\bin\git.exe``

    :param str program:
        The program to search for. Must be an exectable, not a shell built-in.

    :raise FileNotFoundError: if the program cannot be found on the PATH.

    :return: Full path to exectuable.
    :rtype: Path
    """
    # If program is already a perfect path, simply return it.
    if Path(program).exists():
        return Path(program)

    # Get list of executable extensions (Windows).
    exts: List[str] = []
    if os.environ.get("PATHEXT"):
        exts = os.environ["PATHEXT"].split(";")

    # Search paths for program.
    for path in os.get_exec_path():
        testpath = Path(path) / program
        if testpath.exists():
            LOGGER.debug("Found `%s` at `%s`", program, testpath)
            return testpath
        for ext in exts:
            testpath = Path(path) / f"{program}{ext}"
            if testpath.exists():
                LOGGER.debug("Found `%s` at `%s`", program, testpath)
                return testpath

    raise FileNotFoundError(f"Could not find `{program}` on PATH")


def exec_proc(
    args: List[str],
    infile: Path = None,
    outfile: Path = None,
    outfile_mode: str = "w",
    ok_exit_codes: List[int] = [0],
) -> Tuple[int, str, str]:
    """
    Executes a process on the local machine.

    :param List[str] args:
        The arguments, starting with program name, that get passed to Popen.
        This does not support shell commands, only executable files.
    :param str infile:
        Path to a file to pipe into stdin as UTF8 text.
    :param str outfile:
        Path to a file in which to save stdout as UTF8 text.
    :param str outfile_mode:
        Mode to use when writing to outfile.
    :param List[int] ok_exit_codes:
        The list of exit/return codes will not be logged as errors.

    :return: Tuple of exit code, stdout, stderr.
    :rtype: Tuple[int, str, str]
    """
    if not os.path.isfile(args[0]):
        # Find program on PATH.
        args[0] = str(get_command(args[0]))

    stdin: Union[IO, None] = None
    stdout: Union[IO, int] = PIPE

    if infile:
        LOGGER.debug("Opening `%s`.", infile)
        stdin = open(infile, "r", encoding="utf8")
    if outfile:
        LOGGER.debug("Opening `%s`.", outfile)
        stdout = open(outfile, outfile_mode, encoding="utf8")

    # NOTE: PyInstaller adds an entry to LD_LIBRARY_PATH in env during python
    # runtime. This is needed for PyInstaller to work, but it can interfere with
    # subprocess execution as PyInstaller's LD_LIBRARY_PATH differs from the
    # system's LD_LIBRARY_PATH. Clear out that variable for the subprocess's
    # execution as a workaround, otherwise the subprocess might not be able to
    # load any dynamically linked libraries from the system, or might load the
    # wrong libraries from PyInstaller!
    fixenv: dict = os.environ.copy()
    if "LD_LIBRARY_PATH" in fixenv:
        del fixenv["LD_LIBRARY_PATH"]
    LOGGER.info("Running `%s`...", args)
    LOGGER.debug("Running `%s` with ENV: %s", args, fixenv)
    try:
        with Popen(
            args,
            stdin=stdin,
            stdout=stdout,
            stderr=PIPE,
            universal_newlines=True,
            env=fixenv,
        ) as proc:
            # Run process and capture output.
            com_stdout, com_stderr = proc.communicate()
            # Log stdout to debug.
            if com_stdout:
                LOGGER.debug(com_stdout.strip())
            # Log stderr to error, or debug.
            if com_stderr and proc.returncode not in ok_exit_codes:
                LOGGER.error(com_stderr.strip())
            elif com_stderr:
                LOGGER.debug(com_stderr.strip())
    finally:
        if isinstance(stdin, io.IOBase):
            LOGGER.debug("Closing `%s`.", infile)
            stdin.close()
        if isinstance(stdout, io.IOBase):
            LOGGER.debug("Closing `%s`.", outfile)
            stdout.close()

    return (proc.returncode, com_stdout, com_stderr)


def git_branch() -> str:
    """
    Returns current git branch.
    """
    _, out, err = exec_proc(["git", "branch", "--show-current"])
    branch = out.strip("\r\n")
    LOGGER.debug("Git branch `%s`.", branch)
    return branch


def git_ignored(p: Path = None) -> List[Path]:
    """
    Returns a list of absolute file and directory paths ignored by git.
    """
    lp: List[Path] = []

    # Build the command.
    cmd = ["git", "ls-files", "--others", "--directory"]
    if p:
        cmd.append(str(p))

    # Run the command.
    # If git exits with an error, or is not on the path, return an empty list.
    try:
        code, out, err = exec_proc(cmd)
        if code != 0:
            return lp
    except FileNotFoundError:
        return lp

    # Split stdout by newline.
    ls = out.strip("\r\n").split("\n")

    # Convert each entry to a Path.
    for s in ls:
        lp.append(Path(s.strip("\r\n")).resolve())
    LOGGER.debug("Git ignored: `%s`.", lp)

    return lp


def git_tag() -> str:
    """
    Finds the current git tag.
    """
    _, out, err = exec_proc(["git", "describe", "--tags"])
    tag = out.strip("\r\n")
    LOGGER.debug("Git tag `%s`.", tag)
    return tag


def paths_to_deploy(
    r: Path, e: List[Path] = [], i: List[Path] = []
) -> List[Path]:
    """
    Walk the root local directory ``r`` and build a list of absolute file
    and directory paths which should be included in the deployment.

    Paths in ``e`` will be excluded.

    Paths in ``i`` will be included, even if they are excluded by ``e``.
    However, a path in ``i`` which is a subpath of an exluded directory in ``e``
    will still be ignored.

    Any file paths in the returned list must also include their parent directory
    paths within ``r``, so that consumers of this list will know to create them.
    """
    lp: List[Path] = []
    for root, dirs, files in os.walk(r):

        # If subdir is excluded, delete it from the list, so ``os`` will not
        # traverse it. Otherwise, append to the list.
        dirs_copy = dirs.copy()
        for d in dirs_copy:
            dp = Path(os.path.join(root, d))
            dpr = dp.resolve()
            # Force add if included.
            if dpr in i:
                LOGGER.debug("Force include %s", dpr)
                lp.append(dpr)
            # Delete from the list if excluded, so it will not be walked.
            elif (
                dpr in e
                or dpr.name.startswith(".")
                or dpr.name in EXCLUDE_DIRNAMES
            ):
                LOGGER.debug("Force exclude %s", dpr)
                dirs.remove(d)
            # Otherwise add by default.
            else:
                lp.append(dpr)

        # Append any files.
        for f in files:
            fp = Path(os.path.join(root, f))
            fpr = fp.resolve()
            # Force add if included.
            if fpr in i:
                LOGGER.debug("Force include %s", fpr)
                lp.append(fpr)
            # Skip if excluded.
            elif fpr in e:
                LOGGER.debug("Force exclude %s", fpr)
                pass
            # Otherwise add by default.
            else:
                lp.append(fpr)

    return lp
