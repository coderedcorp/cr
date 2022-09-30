"""
Utilities for interacting with CodeRed Cloud host servers.

Copyright (c) 2022 CodeRed LLC.
"""
from pathlib import Path, PurePosixPath
from typing import List, Optional
import stat
import os

from paramiko.client import AutoAddPolicy, SSHClient
from paramiko.sftp_client import SFTPClient
from rich.progress import Progress

from cr.utils import EXCLUDE_DIRNAMES


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
        progress: Progress = None,
    ) -> None:
        """
        Recursively SFTP a Path ``r`` to the server path ``s``.
        File and directory structure within ``r`` is mirrored to ``s``.
        If ``r`` is a file, upload it directly into ``s``.
        Progress bar ``p`` is updated with a task for each file upload.
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
                            f"[cr.progress_print]MKDIR {relative_p}[/]"
                        )
                    sftp.mkdir(str(remote_p), mode=0o770)
            else:
                if progress:
                    progress.print(f"[cr.progress_print]PUT   {relative_p}[/]")
                sftp.put(str(p), str(remote_p))
            if progress:
                progress.update(t, advance=1)

    def get(
        self,
        s: PurePosixPath,
        r: Path,
        progress: Progress = None,
    ) -> None:
        """
        Recursively download a Path ``s`` from the server to local directory ``r``.
        File and directory structure within ``s`` is mirrored to ``r``.
        If ``s`` is a file, download it directly into ``r``.
        Progress bar ``p`` is updated with a task for each file download.
        """
        # Connect.
        sftp = self.open_sftp()

        # If root is a file, get its parent directory.
        if r.is_file():
            r = r.parent

        # Traverse server path and download files.
        if progress:
            t = progress.add_task("Scanning", total=None)

        def get_files(sp: PurePosixPath, t, count: int = 0) -> int:
            """
            Recursively downloads remote dir ``sp``, and returns number of
            files download.
            """
            items = sftp.listdir_attr(str(sp))
            for item in items:
                # Figure out the local path that this remote file should be
                # downloaded to.
                fullpath = sp / item.filename
                relpath = fullpath.relative_to(s)
                localpath = r / relpath

                # Apparently this can be None, according to mypy.
                if item.st_mode is None:
                    raise Exception(f"SFTP stat mode undefined `{fullpath}`.")

                # If it is a directory...
                if stat.S_ISDIR(item.st_mode):
                    # Skip over hidden or excluded dirs.
                    if (
                        item.filename.startswith(".")
                        or item.filename in EXCLUDE_DIRNAMES
                        or item.filename in ["static", "cache", "media"]
                    ):
                        continue

                    # Make a local directly to match the server path.
                    if progress:
                        progress.print(f"[cr.progress_print]MKDIR {relpath}[/]")
                    os.makedirs(localpath, exist_ok=True)

                    # Recursively traverse this directory.
                    count = get_files(fullpath, t, count)

                # Else download the file.
                else:
                    if progress:
                        progress.print(f"[cr.progress_print]GET   {relpath}[/]")
                    sftp.get(str(fullpath), str(localpath))
                    count += 1
                    if progress and t:
                        progress.update(t, advance=1)

            return count

        # Recursively download the files.
        num = get_files(s, t)
        if progress:
            progress.update(t, total=num)
