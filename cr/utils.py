"""
Subprocess and filesystem utilities for working with the local project.

Copyright (c) 2022 CodeRed LLC.
"""
import io
import os
import re
from pathlib import Path
from subprocess import PIPE
from subprocess import Popen
from typing import IO
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from cr import ConfigurationError
from cr import DatabaseType
from cr import LOGGER


EXCLUDE_DIRNAMES = ["__pycache__", "node_modules", "htmlcov", "venv"]
"""
List of directory names to always exclude from deployments.
"""

TEMPLATE_PATH = Path(__file__).parent / "templates"
"""
Templates directory included with this project.
"""

URLSAFE_REGEX = re.compile(
    r"^[a-zA-Z0-9]+[a-zA-Z0-9\-]+[a-zA-Z0-9]+$", flags=re.IGNORECASE
)
"""
Regular expression to determine if a word is usable in DNS/URLs. Letter or
number + at least one other letter or number or dash + letter or number.
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
    infile: Optional[Path] = None,
    outfile: Optional[Path] = None,
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


def git_ignored(p: Optional[Path] = None) -> List[Path]:
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


def template(t: str) -> str:
    """
    Read file ``t`` from the templates directory and return it as a string.
    """
    return (TEMPLATE_PATH / t).read_text()


def is_urlsafe(value: str) -> bool:
    """
    Determines if value is a valid URL, subdomain, etc.
    """
    return bool(URLSAFE_REGEX.match(value))


def check_handle(value: str) -> bool:
    """
    Check if the provided ``value`` could be a valid webapp handle.
    """
    return is_urlsafe(value) and len(value) <= 32


def django_manage_check(p: Path) -> None:
    if not (p / "manage.py").is_file():
        raise FileNotFoundError("manage.py")


def django_requirements_check(p: Path) -> None:
    if not (p / "requirements.txt").is_file():
        raise FileNotFoundError("requirements.txt")


def django_settings_check(p: Path) -> None:
    """
    Given a path to a settings file ``p``, check the contents of the settings file.
    """
    if not p.is_file():
        raise FileNotFoundError(p)
    contents = p.read_text()
    if "VIRTUAL_HOST" not in contents or "DB_HOST" not in contents:
        raise ConfigurationError()


def django_settings_fix(p: Path, db: DatabaseType) -> None:
    """
    Given a path to a settings file ``p``, create and/or rewrite existing
    settings in the expected format.

    ``p`` Should be a sanctioned settings path, e.g. project/settings/prod.py
    """
    project_dir = p.parent.parent
    settings = project_dir / "settings.py"
    settings_dir = project_dir / "settings"
    settings_base = settings_dir / "base.py"

    # If we don't have a settings.py or a settings/base.py, give up.
    if not (settings.is_file() or settings_base.is_file()):
        raise FileNotFoundError(
            "Could not find any Django settings. "
            f"Does the folder contain a Django project named "
            f"`{project_dir.name}`?"
        )

    # Check for settings.py and convert to settings/base.py.
    if settings.is_file() and not settings_base.is_file():
        LOGGER.info("Creating %s", settings_base)
        # Make settings dir.
        settings_dir.mkdir(parents=True, exist_ok=True)
        # Move settings.py to settings/base.py
        settings.rename(settings_base)

    # Ensure paths are correct in settings/base.py
    settings_str = settings_base.read_text()
    # Rewrite BASE_DIR to accurately reflect the location.
    # Django >=3.1 settings have ``BASE_DIR = Path``
    settings_str = re.sub(
        r"^BASE_DIR\s+=\s+Path.+$",
        r"BASE_DIR = Path(__file__).resolve().parent.parent.parent",
        settings_str,
        flags=re.MULTILINE,
    )
    # Django <3.1 and Wagtail settings have ``BASE_DIR = os.path``
    settings_str = re.sub(
        r"^BASE_DIR\s+=\s+os\..+$",
        r"from pathlib import Path\n"
        r"BASE_DIR = Path(__file__).resolve().parent.parent.parent",
        settings_str,
        flags=re.MULTILINE,
    )
    LOGGER.info("Writing to %s", settings_base)
    settings_base.write_text(settings_str)

    # Create desired settings file.
    if not p.exists():
        p.write_text(template("settings-top.py.txt"))

    # If settings file does not look correct, append our recommended.
    settings_str = p.read_text()
    if not re.findall(
        r"os\.environ\[\s*[\'\"]VIRTUAL_HOST[\'\"]\s*\]",
        settings_str,
    ):
        settings_str += "\n"
        settings_str += template("settings.py.txt")
    if not re.findall(
        r"os\.environ\[\s*[\'\"]DB_HOST[\'\"]\s*\]",
        settings_str,
    ):
        settings_str += "\n"
        settings_str += template(f"settings-{db}.py.txt")
    LOGGER.info("Writing to %s", p)
    p.write_text(settings_str)


def django_wsgi_check(p: Path, project: str):
    """
    Checks for existence of a wsgi.py file in the project folder.
    """
    wsgi = p / project / "wsgi.py"
    if not wsgi.is_file():
        wsgi_relative = wsgi.relative_to(p)
        raise FileNotFoundError(wsgi_relative)


def django_wsgi_find(p: Path) -> str:
    """
    Find a subdirectory of ``p`` that contains a wsgi.py file.
    """
    for item in p.iterdir():
        if item.is_dir() and (item / "wsgi.py").is_file():
            return item.name
    raise FileNotFoundError("wsgi.py")


def html_index_check(p: Path) -> None:
    """
    Checks for existence of an index.html file.
    """
    if not (p / "index.html").is_file():
        raise FileNotFoundError("index.html")


def wagtail_settings_fix(p: Path) -> None:
    """
    Given a path to a settings file ``p``, append any Wagtail-specific settings
    if they are missing.
    """
    settings_str = p.read_text()
    if "WAGTAILADMIN_BASE_URL" not in settings_str:
        settings_str = p.read_text()
        settings_str += "\n"
        settings_str += template("settings-wagtail.py.txt")
        LOGGER.info("Writing to %s", p)
        p.write_text(settings_str)


def wordpress_wpconfig_check(p: Path) -> None:
    """
    Checks for existence of a wp-config.php file.
    TODO: Inspect the file similar to Django settings functionality.
    """
    if not (p / "wp-config.php").is_file():
        raise FileNotFoundError("wp-config.php")
