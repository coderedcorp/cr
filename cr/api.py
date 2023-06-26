"""
Utilities to call CodeRed Cloud API.

Copyright (c) 2022 CodeRed LLC.
"""
import json
import time
from http.client import HTTPResponse
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

import certifi
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress

from cr import AppType
from cr import ConfigurationError
from cr import DatabaseType
from cr import DOCS_LINK
from cr import Env
from cr import LOGGER
from cr import USER_AGENT
from cr import UserCancelError
from cr import VERSION
from cr.utils import django_manage_check
from cr.utils import django_requirements_check
from cr.utils import django_settings_check
from cr.utils import django_settings_fix
from cr.utils import django_wsgi_check
from cr.utils import django_wsgi_find
from cr.utils import html_index_check
from cr.utils import wagtail_settings_fix
from cr.utils import wordpress_wpconfig_check


class DatabaseServer:
    def __init__(self, hostname: str, db_type: DatabaseType):
        self.hostname: str = hostname
        self.db_type: DatabaseType = db_type


class Webapp:
    """
    Minimal representation of our Webapp model, with API functions for task
    queueing.
    """

    def __init__(self, handle: str, token: str, env: Env = Env.PROD):
        """
        Loads the webapp info from CodeRed Cloud API.
        """
        status, d = coderedapi(f"/api/webapps/{handle}/", "GET", token)

        self.handle: str = handle
        self.token: str = token
        self.env: Env = env

        # Populate the object from API response.
        self.id: int = d["id"]
        self.app_type: AppType = AppType(d["app_type"])
        self.app_type_name: str = d["app_type_info"]["name"]
        self.container_img: str = d.get("container_img", "")
        self.container_img_using: str = d.get("container_img_using", "")
        self.databases: List[str] = d["databases"]
        self.django_project: str = d["django_project"]
        self.name: str = d["name"]
        self.primary_domain: str = d["primary_domain"]
        self.primary_url: str = d["primary_url"]
        self.prod_dbserver: Optional[DatabaseServer] = None
        self.sftp_prod_domain: str = d["sftp_prod_domain"]
        self.sftp_staging_domain: str = d["sftp_staging_domain"]
        dbdict = d.get("prod_dbserver")
        if dbdict:
            self.prod_dbserver = DatabaseServer(
                hostname=dbdict["hostname"],
                db_type=DatabaseType(dbdict["db_type"]),
            )
        dbdict = d.get("staging_dbserver")
        if dbdict:
            self.staging_dbserver = DatabaseServer(
                hostname=dbdict["hostname"],
                db_type=DatabaseType(dbdict["db_type"]),
            )

    @property
    def url(self) -> str:
        """
        Return the URL of this website based on environment.
        """
        if self.env == Env.STAGING:
            return f"https://{self.handle}.staging.codered.cloud/"
        return self.primary_url

    @property
    def database(self) -> DatabaseServer:
        """
        Return the DatabaseServer based on environment.
        """
        return getattr(self, f"{self.env}_dbserver")

    def local_check_path(self, p: Path, c: Optional[Console]) -> None:
        """
        Check that provided Path ``p`` appears to contain a valid AppType
        project. If Console ``c`` is provided, ask the user for input to
        resolve any problems.

        Raises FileNotFoundError, NotADirectoryError, or UserCancelError.
        """
        if not p.exists():
            raise FileNotFoundError(p)
        if not p.is_dir():
            raise NotADirectoryError(f"Expected a directory: `{p}`")

        if self.app_type in [
            AppType.CODEREDCMS,
            AppType.DJANGO,
            AppType.WAGTAIL,
        ]:
            self.local_check_django(p, c)
        elif self.app_type == AppType.WORDPRESS:
            self.local_check_wordpress(p, c)
        elif self.app_type == AppType.HTML:
            self.local_check_html(p, c)

    def local_check_django(self, p: Path, c: Optional[Console] = None) -> None:
        """
        Checks that a Django or Wagtail project contains correct files and
        structure, and offers to fix files when possible.
        """
        # Check ``manage.py`` file.
        try:
            django_manage_check(p)
        except FileNotFoundError as err:
            _prompt_filenotfound(err, c)

        # Check ``requirements.txt`` file.
        try:
            django_requirements_check(p)
        except FileNotFoundError as err:
            _prompt_filenotfound(err, c)

        # Check for a ``wsgi.py`` file in the Django project folder.
        try:
            django_wsgi_check(p, self.django_project)
        except FileNotFoundError as err:
            LOGGER.warning("%s file does not exist!", err)
            # Guess what the correct Django project might be.
            try:
                djp = django_wsgi_find(p)
            except FileNotFoundError:
                djp = ""
            if c and djp:
                answer = c.input(
                    "Webapp is configured with a Django project named "
                    f"`{self.django_project}` on CodeRed Cloud, but it looks "
                    f"like this project is named `{djp}`.\n"
                    f"[prompt.choices]1[/]) Set Django project on CodeRed Cloud to `{djp}`.\n"
                    "[prompt.choices]2[/]) Continue anyways.\n"
                    "[prompt.choices]3[/]) Cancel and quit.\n"
                    "\n"
                    r"Choose an option to continue: [prompt.choices](1/2/3)[/] ",
                ).strip()
                if answer == "1":
                    self.api_set_django_project(djp)
                elif answer == "2":
                    pass
                else:
                    raise UserCancelError()
            elif c:
                _prompt_filenotfound(err, c)

        # Check settings file.
        settings = p / self.django_project / "settings" / f"{self.env}.py"
        settings_rel = settings.relative_to(p)
        fix_me = False
        try:
            django_settings_check(settings)
        # If settings file does not exist, offer to create it.
        except FileNotFoundError:
            LOGGER.warning("Settings file does not exist! %s", settings)
            if c:
                answer = c.input(
                    f"Missing settings file `{settings_rel}`. "
                    "Without this your app will not deploy correctly.\n"
                    "[prompt.choices]1[/]) Create recommended settings.\n"
                    "[prompt.choices]2[/]) Continue anyways.\n"
                    "[prompt.choices]3[/]) Cancel and quit.\n"
                    "\n"
                    r"Choose an option to continue: [prompt.choices]\[1/2/3][/] ",
                ).strip()
                if answer == "1":
                    fix_me = True
                elif answer == "2":
                    pass
                else:
                    raise UserCancelError()
        # If settings file is misconfigured, offer to fix it.
        except ConfigurationError:
            LOGGER.warning("Settings file may be misconfigured. %s", settings)
            if (
                c
                and "y"
                == c.input(
                    f"Settings file `{settings_rel}` may be misconfigured. "
                    "Correct it? [prompt.choices](y/N)[/] "
                ).lower()
            ):
                fix_me = True
        if fix_me:
            django_settings_fix(settings, self.database.db_type)
            if self.app_type in [AppType.CODEREDCMS, AppType.WAGTAIL]:
                wagtail_settings_fix(settings)

    def local_check_html(self, p: Path, c: Optional[Console] = None) -> None:
        try:
            html_index_check(p)
        except FileNotFoundError as err:
            _prompt_filenotfound(err, c)

    def local_check_wordpress(
        self, p: Path, c: Optional[Console] = None
    ) -> None:
        try:
            wordpress_wpconfig_check(p)
        except FileNotFoundError as err:
            _prompt_filenotfound(err, c)

    def api_set_django_project(self, name: str) -> None:
        """
        PATCH the webapp on coderedapi and set the local django_project.
        """
        _, d = coderedapi(
            f"/api/webapps/{self.handle}/",
            "PATCH",
            self.token,
            {"custom_django_project": name},
        )
        self.django_project = d["django_project"]

    def api_get_sftp_password(self) -> str:
        """
        Resets and retrieves the tenant's SFTP password for ``env``.
        """
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": self.env.value,
                "task_type": "resetpassword",
            },
        )

        re_data = d.get("returned_data", {})
        if "password" in re_data:
            return re_data["password"]
        if "error" in d:
            error = d["error"]
            raise Exception(f"Host Error: {error}")
        if "error" in re_data:
            error = re_data["error"]
            raise Exception(f"Host Error: {error}")
        raise Exception("SFTP password not available. Please contact support.")

    def api_queue_deploy(self) -> int:
        """
        Queue a deploy task and return the task ID.
        """
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": self.env.value,
                "task_type": "init",
            },
        )
        if status >= 400:
            raise Exception("Error queueing deploy task.")
        LOGGER.info("Task created: %s", d)
        return d["id"]

    def api_queue_restart(self) -> int:
        """
        Queue a restart task and return the task ID.
        """
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": self.env.value,
                "task_type": "restart",
            },
        )
        if status >= 400:
            raise Exception("Error queueing restart task.")
        LOGGER.info("Task created: %s", d)
        return d["id"]

    def api_get_logs(self, since: int = 0) -> dict:
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": self.env.value,
                "task_type": "getlog",
                "query_params": {"since": since},
            },
        )
        if status >= 400:
            raise Exception("Error getting deployment log.")
        return d["returned_data"]

    def api_poll_logs(self, progress: Progress) -> None:
        """
        Poll deployment logs until EOT is found, or a fixed amount of time,
        and print to Progress.
        """
        kill = False
        since = 0
        for i in range(18):
            data = self.api_get_logs(since=since)
            logs = data["logs"]
            if not logs:
                kill = True
            for line in logs:
                text = line["log"]
                since = line["datetime"]
                style = ""
                if line["source"] == "stderr":
                    style = "logging.level.warning"
                progress.print(
                    f"> {text}",
                    markup=False,
                    highlight=False,
                    style=style,
                )
                if "\x04" in text:
                    kill = True
                time.sleep(0.1)
            if kill:
                break
            time.sleep(10)

    def api_get_task(self, task_id: int) -> dict:
        """
        Check a task's status and return the dict from coderedapi.
        """
        status, d = coderedapi(
            f"/api/tasks/{task_id}/",
            "GET",
            self.token,
        )
        if status >= 400:
            raise Exception(f"Could not query task ID {task_id}")
        LOGGER.info("Task: %s", d)
        return d

    def api_poll_task(self, task_id: int) -> dict:
        """
        Blocking function to poll a task every 10 seconds until it completes.

        Returns the completed task dict.

        Raises TimeoutError if the task does not complete after 3 minutes.
        """
        for i in range(18):
            d = self.api_get_task(task_id)
            if d["status"] == "completed":
                return d
            time.sleep(10)
        raise TimeoutError(f"Task ID {task_id} has not completed.")


def _response_to_json(r: Union[HTTPResponse, HTTPError]) -> dict:
    """
    Parses a JSON response from an HTTPResponse or HTTPError as a dictionary.
    """
    text = r.read().decode("utf8")
    d = {}
    if text:
        d = json.loads(text)
    LOGGER.debug("Parsed: %s", d)
    return d


def request_json(
    url: str,
    method: str = "GET",
    headers: Dict[str, str] = {},
    data: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> Tuple[int, dict]:
    """
    Makes an HTTP request and parses the JSON response.
    """
    # Update headers to request JSON and specify user agent.
    headers["User-Agent"] = USER_AGENT
    if "Accept" not in headers:
        headers["Accept"] = "application/json"
    if data and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    req = Request(
        url,
        method=method,
        headers=headers,
    )
    if data:
        req.data = bytes(json.dumps(data), encoding="utf8")

    # Open the request and read the response.
    try:
        r = urlopen(req, timeout=timeout, cafile=certifi.where())
        d = _response_to_json(r)
        code = r.code
        LOGGER.info("%s %s %d", method, url, code)
    # Non-200 statuses can be read similarly.
    except HTTPError as err:
        LOGGER.error("%s %s %s", method, url, err)
        d = _response_to_json(err)
        code = err.code

    return (code, d)


def coderedapi(
    endpoint: str,
    method: str,
    token: str,
    data: Optional[dict] = None,
    ok: List[int] = [200, 201],
) -> Tuple[int, Dict[str, Any]]:
    """
    Calls CodeRed Cloud API and returns a tuple of:
    (HTTP status code, dict of returned JSON).

    Raises a human-readable exception if the status code is not in ``ok``.
    """
    endpoint = endpoint.lstrip("/")
    try:
        code, d = request_json(
            f"https://app.codered.cloud/{endpoint}",
            method=method,
            headers={
                "Authorization": f"Token {token}",
            },
            data=data,
        )
    except Exception:
        raise Exception(
            "Error contacting CodeRed API. Please try again shortly."
        )

    # If the code is not within the list of expected status codes, raise an
    # error with the API's error message.
    if ok and code not in ok:
        if "detail" in d:
            raise Exception(d["detail"])
        if "error" in d:
            raise Exception(d["error"])
        raise Exception(f"CodeRed Cloud API responded with: {code}")

    return (code, d)


def check_update(c: Optional[Console] = None) -> bool:
    """
    Check if a new version is available and print to Console ``c``.
    If this fails or takes longer than 1 second, simply ignore it.
    """
    try:
        _, gh = request_json(
            "https://api.github.com/repos/coderedcorp/cr/releases/latest",
            timeout=1,
        )
        newver = gh["tag_name"].strip("vV")
        if VERSION != newver:
            if c:
                p = Panel(
                    f"Newer version of cr [cr.code]{newver}[/] is available!\n"
                    f"See: {DOCS_LINK}",
                    border_style="cr.update_border",
                )
                c.print(p)
            return True
    except Exception as exc:
        LOGGER.warning("Error checking for update.", exc_info=exc)
    return False


def _prompt_filenotfound(
    err: FileNotFoundError, c: Optional[Console] = None
) -> None:
    """
    If Console ``c`` is provided, ask the user to continue when a file is not
    found.
    """
    LOGGER.warning("%s file does not exist!", err)
    if (
        c
        and "y"
        != c.input(
            f"Missing `{err}` file. "
            "Without this your app will not deploy correctly. "
            "Continue anyways? [prompt.choices](y/N)[/] ",
        ).lower()
    ):
        raise UserCancelError()
