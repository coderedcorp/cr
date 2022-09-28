"""
Loads runtime variables from various config files.

Copyright (c) 2022 CodeRed LLC.
"""
from pathlib import Path
from typing import List, Optional
import configparser
import os

from cr import LOGGER


# List of config values to consider secret.
# They should never be printed or logged.
_SECRETS = ["token"]


_CONFIG = configparser.ConfigParser(default_section="cr")


def load_config(lp: List[Path] = []) -> List[str]:
    """
    Reads config files from pre-defined paths, plus any additional paths ``lp``.
    """
    return _CONFIG.read(
        [
            Path("~/.cr.ini").expanduser().resolve(),
            Path(".cr.ini").resolve(),
            *lp,
        ]
    )


def config(k, w: str = "cr", f: str = None) -> Optional[str]:
    """
    Queries the various config files for a key ``k`` in either the default
    section [cr], or overridden in a webapp section ``w`` [``w``].

    Priority is as follows, each bullet in the list overriding those before it.

    * Environment variables
    * [cr] section in ~/.cr.ini
    * [cr] section in ./.cr.ini
    * [``w``] section in ~/.cr.ini
    * [``w``] section in ./.cr.ini

    If the key is not found, return fallback ``f``.
    """
    val = f

    # Query secret configs from env vars first.
    if k in _SECRETS:
        val = os.environ.get(f"CR_{k.upper()}", val)

    # Query the config, which will override any env vars.
    if w in _CONFIG:
        val = _CONFIG[w].get(k, val)
    else:
        val = _CONFIG.defaults().get(k, val)

    LOGGER.debug("Config `%s`: `%s`", k, val)

    return val


def config_bool(k, w: str = "cr", f: bool = None) -> Optional[bool]:
    """
    Queries a config and parses it as a boolean. Acceptable case-insensitive
    values are: on, off, yes, no, true, false, 0, 1
    """
    val = config(k, w)
    if val is None:
        return f
    return val.lower() in ["yes", "on", "true", "1"]


def config_path_list(k, w: str = "cr") -> List[Path]:
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
            lp.append(Path(line.strip(" \t\r\n")).expanduser().resolve())
    return lp