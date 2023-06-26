"""
Utilities for interacting with CodeRed Cloud host servers.

Copyright (c) 2022 CodeRed LLC.
"""
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import List
from typing import Optional

from paramiko.client import AutoAddPolicy
from paramiko.client import SSHClient
from paramiko.sftp_client import SFTPClient
from rich.progress import Progress

from cr import LOGGER
from cr.utils import EXCLUDE_DIRNAMES


@dataclass
class TransferPath:
    """
    Hold info about a file that exists on both the server and locally.
    """

    local: Path
    relative: PurePosixPath
    remote: PurePosixPath
    remote_st_mode: Optional[int]


class Server:
    """
    Represents a CodeRed Cloud host server.
    """

    def __init__(self, host: str, user: str, passwd: str):
        self.host = host
        self.user = user
        self.passwd = passwd
        self._client: Optional[SSHClient] = None
        self._sftp: Optional[SFTPClient] = None

    def connect(self) -> SSHClient:
        """
        Returns an open/connected ``SSHClient``.
        """
        if self._client:
            return self._client
        c = SSHClient()
        c.set_missing_host_key_policy(AutoAddPolicy)
        c.connect(
            hostname=self.host,
            port=22,
            username=self.user,
            password=self.passwd,
            look_for_keys=False,
        )
        self._client = c
        return c

    def open_sftp(self) -> SFTPClient:
        if self._sftp:
            return self._sftp
        client = self.connect()
        sftp = client.open_sftp()
        self._sftp = sftp
        return sftp

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self._client = None

    def put(
        self,
        lp: List[Path],
        r: Path,
        s: PurePosixPath,
        progress: Optional[Progress] = None,
    ) -> None:
        """
        Upload a list of paths ``lp``, relative to local root Path ``r`` to
        the server path ``s``. File and directory structure within ``r`` is
        mirrored to ``s``. Progress bar ``p`` is updated with a task for each
        file upload.
        """
        # Connect.
        sftp = self.open_sftp()

        # If root is a file, get its parent directory.
        if r.is_file():
            r = r.parent

        # Upload files from the list.
        if progress:
            t = progress.add_task("Uploading", total=len(lp))
        for p in lp:
            # Figure out remote path by joining server path ``s`` with the
            # relative local path of ``p``.
            p = p.resolve()
            relative_p = p.relative_to(r.resolve())
            remote_p = s / relative_p
            if p.is_dir():
                try:
                    sftp.lstat(str(remote_p))
                except FileNotFoundError:
                    if progress:
                        progress.print(
                            f"MKDIR {relative_p}",
                            style="cr.progress_print",
                        )
                    sftp.mkdir(str(remote_p), mode=0o770)
            else:
                if progress:
                    progress.print(
                        f"PUT   {relative_p}",
                        style="cr.progress_print",
                    )
                sftp.put(str(p), str(remote_p))
            if progress:
                progress.update(t, advance=1)

    def get(
        self,
        s: PurePosixPath,
        r: Path,
        e: List[PurePosixPath] = [],
        progress: Optional[Progress] = None,
    ) -> None:
        """
        Recursively download a Path ``s`` from the server to local directory ``r``.
        File and directory structure within ``s`` is mirrored to ``r``.
        If ``s`` is a file, download it directly into ``r``.
        Do not download any directories in ``e`` (relative to ``s``).
        Progress bar ``p`` is updated with a task for each file download.
        """
        # Connect.
        sftp = self.open_sftp()

        # If root is a file, get its parent directory.
        if r.is_file():
            r = r.parent

        if progress:
            t = progress.add_task("Finding files", total=None)

        def walk_remote(
            sp: PurePosixPath,
            lp: List[TransferPath] = [],
        ) -> List[TransferPath]:
            """
            Recursively scan remote dir ``sp``, and return list of files and
            directories to download ``lp``.
            """
            items = sftp.listdir_attr(str(sp))
            for item in items:
                # Figure out the local path that this remote file should be
                # downloaded to.
                fullpath = sp / item.filename
                relpath = fullpath.relative_to(s)
                localpath = r / relpath
                tp = TransferPath(
                    relative=relpath,
                    remote=fullpath,
                    remote_st_mode=item.st_mode,
                    local=localpath,
                )

                # Apparently this can be None, according to mypy.
                if item.st_mode is None:
                    LOGGER.warning(f"SFTP stat mode undefined `{fullpath}`.")
                    lp.append(tp)

                # If it is a directory.
                elif stat.S_ISDIR(item.st_mode):
                    # Skip over hidden or excluded dirs.
                    if (
                        relpath in e
                        or item.filename.startswith(".")
                        or item.filename in EXCLUDE_DIRNAMES
                    ):
                        continue

                    # Add to the list.
                    lp.append(tp)

                    # Recursively traverse this directory.
                    lp = walk_remote(fullpath, lp)

                # If it is a file.
                elif stat.S_ISREG(item.st_mode):
                    # Skip over excluded files.
                    if relpath in e:
                        continue

                    # Add to the list.
                    lp.append(tp)

            return lp

        # Lookup ``s`` on the server.
        st = sftp.lstat(str(s))

        # If ``s`` is a directory, recursively build a list of files and
        # directories to download.
        if st.st_mode is not None and stat.S_ISDIR(st.st_mode):
            ltp = walk_remote(s)
        # Otherwise queue just the file.
        else:
            ltp = [
                TransferPath(
                    remote=s,
                    remote_st_mode=st.st_mode,
                    relative=PurePosixPath(s.name),
                    local=r / s.name,
                )
            ]

        # Complete scan task, and add a download task.
        if progress:
            num = len(ltp)
            progress.update(t, total=num, completed=num)
            t = progress.add_task("Downloading", total=num)

        os.makedirs(r, exist_ok=True)
        for tp in ltp:
            # If it doesn't have a mode, it is probably a broken file.
            if tp.remote_st_mode is None:
                if progress:
                    progress.print(f"[cr.progress_print]SKIP  {tp.relative}[/]")
            # Make a local directory to match the server path.
            elif stat.S_ISDIR(tp.remote_st_mode):
                if progress:
                    progress.print(f"[cr.progress_print]MKDIR {tp.relative}[/]")
                os.makedirs(tp.local, exist_ok=True)
            # Download the file.
            elif stat.S_ISREG(tp.remote_st_mode):
                if progress:
                    progress.print(f"[cr.progress_print]GET   {tp.relative}[/]")
                sftp.get(str(tp.remote), str(tp.local))
            # Update the progress bar.
            if progress:
                progress.update(t, advance=1)
