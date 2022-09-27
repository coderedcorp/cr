"""
Utilities for interacting with CodeRed Cloud host servers.

Copyright (c) 2022 CodeRed LLC.
"""
from pathlib import Path, PurePosixPath
from typing import List, Optional

from paramiko.client import AutoAddPolicy, SSHClient
from paramiko.sftp_client import SFTPClient
from rich.progress import Progress


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
    ):
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
