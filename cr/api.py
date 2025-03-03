"""
Utilities to call CodeRed Cloud API.

Copyright (c) 2022-2024 CodeRed LLC.
"""

import json
import ssl
import time
from http.client import HTTPResponse
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

import certifi
from rich.panel import Panel
from rich.progress import Progress

from cr import DOCS_LINK
from cr import LOGGER
from cr import PROD_BRANCHES
from cr import STAGING_BRANCHES
from cr import USER_AGENT
from cr import VERSION
from cr import AppType
from cr import ConfigurationError
from cr import DatabaseType
from cr import Env
from cr import UserCancelError
from cr.rich_utils import Console
from cr.utils import django_manage_check
from cr.utils import django_requirements_check
from cr.utils import django_run_check
from cr.utils import django_run_migratecheck
from cr.utils import django_settings_check
from cr.utils import django_settings_fix
from cr.utils import django_wsgi_check
from cr.utils import django_wsgi_find
from cr.utils import git_branch
from cr.utils import git_uncommitted
from cr.utils import git_unpushed
from cr.utils import html_index_check
from cr.utils import wagtail_settings_fix
from cr.utils import wordpress_wpconfig_check


class Client(NamedTuple):
    id: int
    name: str
    address: str
    address_city: str
    address_state: str
    address_postal: str
    address_country: str


class DatabaseServer(NamedTuple):
    hostname: str
    db_type: DatabaseType


class WebServer(NamedTuple):
    hostname: str
    internet_gateway: str
    public_ip4: str


class Webapp:
    """
    Minimal representation of our Webapp model, with API functions for task
    queueing.
    """

    def __init__(
        self,
        handle: str,
        token: str,
        env: Env = Env.PROD,
        from_dict: Optional[dict] = None,
    ):
        """
        Loads the webapp info from CodeRed Cloud API.
        """
        if from_dict:
            d = from_dict
        else:
            _, d = coderedapi(f"/api/webapps/{handle}/", "GET", token)

        self.handle: str = handle
        self.token: str = token
        self.env: Env = env

        # Populate the object from API response.
        self.id: int = d["id"]
        self._client: Optional[Client] = None
        self.app_type: AppType = AppType(d["app_type"])
        self.app_type_name: str = d["app_type_info"]["name"]
        self.client_id: int = d["client"]
        self.container_img: str = d.get("container_img", "")
        self.container_img_using: str = d.get("container_img_using", "")
        self.databases: List[str] = d["databases"]
        self.django_project: str = d["django_project"]
        self.feature_ssh: bool = d.get("feature_ssh", False)
        self.name: str = d["name"]
        self.primary_domain: str = d["primary_domain"]
        self.primary_url: str = d["primary_url"]
        self.prod_dbserver: Optional[DatabaseServer] = None
        self.prod_server: Optional[WebServer] = None
        self.sftp_prod_domain: str = d["sftp_prod_domain"]
        self.sftp_staging_domain: str = d["sftp_staging_domain"]
        self.staging_dbserver: Optional[DatabaseServer] = None
        self.staging_server: Optional[WebServer] = None
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
        serverdict = d.get("prod_server")
        if serverdict:
            self.prod_server = WebServer(
                hostname=serverdict["hostname"],
                internet_gateway=serverdict["internet_gateway"],
                public_ip4=serverdict["public_ip4"],
            )
        serverdict = d.get("staging_server")
        if serverdict:
            self.staging_server = WebServer(
                hostname=serverdict["hostname"],
                internet_gateway=serverdict["internet_gateway"],
                public_ip4=serverdict["public_ip4"],
            )

    @classmethod
    def all(cls, token: str) -> List["Webapp"]:
        """
        Returns a list of all webapps this token can access.
        """
        _, result = coderedapi("/api/webapps/", "GET", token)
        wlist = []
        for item in result:
            wlist.append(cls(item.get("handle"), token, from_dict=item))
        return wlist

    @property
    def client(self) -> Client:
        if not self._client:
            _, d = coderedapi(
                f"/api/clients/{self.client_id}/", "GET", self.token
            )
            self._client = Client(
                d["id"],
                d["name"],
                d["address"],
                d["address_city"],
                d["address_state"],
                d["address_postal"],
                d["address_country"],
            )
        return self._client

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

    def local_check(self, p: Path, c: Optional[Console]) -> None:
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
            if c and c.prompt_yn(
                f"Settings file `{settings_rel}` may be misconfigured. "
                "Correct it?",
                nouser=False,
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

    def local_predeploy(self, p: Path, c: Optional[Console]) -> None:
        """
        Runs various common checks before a deployment, to help prevent the
        user from creating a broken deployment or deviating from the recommended
        development process.
        """

        # Check git branch.
        b = git_branch()
        if b and self.env == Env.STAGING and b not in STAGING_BRANCHES:
            if c and not c.prompt_yn(
                f"You are deploying to STAGING from the `{b}` branch!\n"
                "It is recommended to deploy from a dedicated staging branch.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()
        if b and self.env == Env.PROD and b not in PROD_BRANCHES:
            if c and not c.prompt_yn(
                f"You are deploying to PROD from the `{b}` branch!\n"
                "It is recommended to deploy from a dedicated production branch.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()

        # Check for un-commited changes.
        if git_uncommitted():
            if c and not c.prompt_yn(
                "You have changes which are NOT COMMITTED to git!\n"
                "It is recommended to commit and push your changes before deploying.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()

        # Check for un-pushed.
        if git_unpushed():
            if c and not c.prompt_yn(
                "You have changes which are NOT PUSHED to git!\n"
                "It is recommended to push your changes before deploying.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()

        if self.app_type in [
            AppType.CODEREDCMS,
            AppType.DJANGO,
            AppType.WAGTAIL,
        ]:
            self.local_predeploy_django(p, c)

    def local_predeploy_django(self, p: Path, c: Optional[Console]) -> None:
        """
        Runs Django-specific checks before a deployment, to help prevent the
        user from creating a broken deployment.
        """
        if c:
            c.print("Checking Django project for potential problems...", end="")
        try:
            ok, output = django_run_check(p)
        except FileNotFoundError:
            ok = False
            output = "Could not find python on this system."
        if c and ok:
            c.print(" [cr.success]OK[/]")
        if c and not ok:
            c.print(" [cr.fail]FAIL[/]")
            if not c.prompt_yn(
                "Django check returned the following errors:\n\n"
                f"{output}\n\n"
                "TIP: be sure to activate your virtual environment and install requirements.txt.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()

        # Check migrations.
        if c:
            c.print("Checking for missing migrations...", end="")
        try:
            ok, output = django_run_migratecheck(p)
        except FileNotFoundError:
            ok = False
            output = "Could not find python on this system."
        if c and ok:
            c.print(" [cr.success]OK[/]")
        if c and not ok:
            c.print(" [cr.fail]FAIL[/]")
            if not c.prompt_yn(
                f"\n{output}\n\n"
                "TIP: did you forget to run `manage.py makemigrations`?\n"
                "TIP: be sure to activate your virtual environment and install requirements.txt.\n"
                "Continue anyways?",
                nouser=True,
            ):
                raise UserCancelError()

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
                "agent_args": {"since": since},
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
                time.sleep(0.05)
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
        Blocking function to poll a task every 10
        seconds until it completes or errors out.

        Returns the completed or errored task dict.

        Raises TimeoutError if the task does not complete after 3 minutes.
        """
        for i in range(18):
            d = self.api_get_task(task_id)
            if d["status"] != "queued":
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
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    try:
        r = urlopen(req, timeout=timeout, context=context)
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
) -> Tuple[int, Any]:
    """
    Calls CodeRed Cloud API and returns a tuple of:
    (HTTP status code, dict or list of returned JSON).

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


def check_update(c: Optional[Console] = None) -> Tuple[bool, Optional[str]]:
    """
    Check if a new version is available and print to Console ``c``.
    If this fails or takes longer than 1 second, simply ignore it.

    Returns tuple of (bool, new version string).
    """
    try:
        _, gh = request_json(
            "https://api.github.com/repos/coderedcorp/cr/releases/latest",
            timeout=1,
        )
        newver = gh["tag_name"].strip("vV")
        # Compare ``X.Y`` semantic versions.
        is_newer = False
        my_maj = int(VERSION.split(".")[0])
        my_min = int(VERSION.split(".")[1])
        gh_maj = int(newver.split(".")[0])
        gh_min = int(newver.split(".")[1])
        if gh_maj > my_maj:
            is_newer = True
        if (gh_maj == my_maj) and (gh_min > my_min):
            is_newer = True
        if is_newer:
            if c:
                p = Panel(
                    f"Newer version of cr [cr.code]{newver}[/] is available!\n"
                    f"See: {DOCS_LINK}",
                    border_style="cr.update_border",
                )
                c.print(p)
            return (True, newver)
        return (False, newver)
    except Exception as exc:
        LOGGER.warning("Error checking for update.", exc_info=exc)
    return (False, None)


def _prompt_filenotfound(
    err: FileNotFoundError, c: Optional[Console] = None
) -> None:
    """
    If Console ``c`` is provided, ask the user to continue when a file is not
    found.
    """
    LOGGER.warning("%s file does not exist!", err)
    if c and not c.prompt_yn(
        f"Missing `{err}` file.\n"
        "Without this your app will not deploy correctly. "
        "Continue anyways?",
        nouser=False,
    ):
        raise UserCancelError()
