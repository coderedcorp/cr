"""
Loads runtime variables from various config files.

Copyright (c) 2022 CodeRed LLC.
"""
import configparser
import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import List
from typing import Optional

from cr import LOGGER


# List of config values to consider secret.
# They should never be printed or logged.
_SECRETS = ["token"]


_CONFIG = configparser.ConfigParser(default_section="cr")


def load_config(lp: List[Path] = []) -> List[str]:
    """
    Reads config files from pre-defined paths, plus any additional paths ``lp``.
    """
    read = _CONFIG.read(
        [
            Path("~/.cr.ini").expanduser().resolve(),
            Path(".cr.ini").resolve(),
            *lp,
        ]
    )
    LOGGER.info("Read config files: %s", read)
    return read


def config(k, w: str = "cr", f: Optional[str] = None) -> Optional[str]:
    """
    Queries the various config files for a key ``k`` in either the default
    section [cr], or overridden in a webapp section ``w`` [``w``].

    Priority is as follows, each bullet in the list overriding those before it.

    * [cr] section in ~/.cr.ini
    * [cr] section in ./.cr.ini
    * [``w``] section in ~/.cr.ini
    * [``w``] section in ./.cr.ini
    * Environment variables

    If the key is not found, return fallback ``f``.
    """
    val = f

    # Query the config files.
    if w in _CONFIG:
        val = _CONFIG[w].get(k, val)
    else:
        val = _CONFIG.defaults().get(k, val)

    # Query secret configs from env vars to override files.
    if k in _SECRETS:
        val = os.environ.get(f"CR_{k.upper()}", val)

    LOGGER.debug("Config `%s`: `%s`", k, val)

    return val


def config_bool(k, w: str = "cr", f: Optional[bool] = None) -> Optional[bool]:
    """
    Queries a config and parses it as a boolean. Acceptable case-insensitive
    values are: on, off, yes, no, true, false, 0, 1
    """
    val = config(k, w)
    if val is None:
        return f
    return val.lower() in ["yes", "on", "true", "1"]


def config_path_list(k, w: str = "cr", f: List[Path] = []) -> List[Path]:
    """
    Queries a multi-line config (newline separated and indented) and returns
    a list of resolved paths.

    Multi-lines should be formatted as so:

        [section]
        key =
            line1
            line2
        another-key = ...
    """
    lp = []
    val = config(k, w)
    if val:
        for line in val.split("\n"):
            line = line.strip(" \t\r\n")
            if line:
                lp.append(Path(line).expanduser().resolve())
    if not lp:
        return f
    return lp


def config_pureposixpath_list(
    k, w: str = "cr", f: List[PurePosixPath] = []
) -> List[PurePosixPath]:
    """
    Queries a multi-line config (newline separated and indented) and returns
    a list of paths.

    Multi-lines should be formatted as so:

        [section]
        key =
            line1
            line2
        another-key = ...
    """
    lp = []
    val = config(k, w)
    if val:
        for line in val.split("\n"):
            line = line.strip(" \t\r\n")
            if line:
                lp.append(PurePosixPath(line))
    if not lp:
        return f
    return lp
