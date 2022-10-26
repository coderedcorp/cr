"""
Utilities to call CodeRed Cloud API.

Copyright (c) 2022 CodeRed LLC.
"""
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import enum
import json
import re
import time

from rich.console import Console
from rich.progress import Progress
from rich.panel import Panel
import certifi

from cr import VERSION, DOCS_LINK, LOGGER, USER_AGENT, UserCancelError
from cr.utils import get_template


class AppType(enum.Enum):
    CODEREDCMS = "coderedcms"
    DJANGO = "django"
    HTML = "html"
    WAGTAIL = "wagtail"
    WORDPRESS = "wordpress"

    def __str__(self):
        return self.value


class DatabaseType(enum.Enum):
    MARIADB = "mariadb"
    POSTGRES = "postgres"

    def __str__(self):
        return self.value


class DatabaseServer:
    def __init__(self, hostname: str, db_type: DatabaseType):
        self.hostname: str = hostname
        self.db_type: DatabaseType = db_type


class Env(enum.Enum):
    PROD = "prod"
    STAGING = "staging"

    def __str__(self):
        return self.value


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
        Validity check for a provided Path ``p``. If Console ``c`` is provided,
        ask the user to continue.

        * Checks if ``p`` exists and is a directory.

        * Check that ``p`` appears to contain an AppType project. If Console
          ``c`` is provided, ask the user to continue.

        * For Django-based projects, check for a correct manage.py, wsgi.py, and
          settings files.

        Raises FileNotFoundError, NotADirectoryError, or UserCancelError.
        """
        if not p.exists():
            raise FileNotFoundError(p)
        if not p.is_dir():
            raise NotADirectoryError(f"Expected a directory: `{p}`")

        # Check for app_type's most obvious file in project root.
        if self.app_type in [
            AppType.CODEREDCMS,
            AppType.DJANGO,
            AppType.WAGTAIL,
        ]:
            project_file = p / "manage.py"
        elif self.app_type == AppType.WORDPRESS:
            project_file = p / "wp-config.php"
        elif self.app_type == AppType.HTML:
            project_file = p / "index.html"
        else:
            raise Exception(f"Invalid AppType `{self.app_type}`.")
        project_file_relative = project_file.relative_to(p)
        if not project_file.is_file():
            LOGGER.warning(
                "%s project missing file `%s`.",
                self.app_type_name,
                project_file,
            )
            if (
                c
                and "y"
                != c.input(
                    f"Your {self.app_type_name} project is missing a "
                    f"`{project_file_relative}` file. "
                    "Without this your app will not deploy correctly. "
                    r"Continue anyways? [prompt.choices]\[y/N][/] ",
                ).lower()
            ):
                raise UserCancelError()

        # The following checks are only for Django-based projects.
        if self.app_type not in [
            AppType.CODEREDCMS,
            AppType.DJANGO,
            AppType.WAGTAIL,
        ]:
            return

        # Check ``requirements.txt`` file.
        req = p / "requirements.txt"
        if not req.is_file():
            LOGGER.warning("requirements.txt file does not exist!")
            if (
                c
                and "y"
                != c.input(
                    "Missing `requirements.txt` file. "
                    "Without this your app will not deploy correctly. "
                    r"Continue anyways? [prompt.choices]\[y/N][/] ",
                ).lower()
            ):
                raise UserCancelError()

        # Check for a ``wsgi.py`` file in the Django project folder.
        wsgi = p / self.django_project / "wsgi.py"
        wsgi_relative = wsgi.relative_to(p)
        if not wsgi.is_file():
            LOGGER.warning("WSGI file does not exist! %s", wsgi)
            if c:
                # Guess what the correct Django project might be.
                djp = ""
                for item in p.iterdir():
                    if item.is_dir() and (item / "wsgi.py").is_file():
                        djp = item.name
                if djp:
                    answer = c.input(
                        "Webapp is configured with a Django project named "
                        f"`{self.django_project}` on CodeRed Cloud, but it looks "
                        f"like this project is named `{djp}`.\n"
                        f"[prompt.choices]1[/]) Set Django project on CodeRed Cloud to `{djp}`.\n"
                        "[prompt.choices]2[/]) Continue anyways.\n"
                        "[prompt.choices]3[/]) Cancel and quit.\n"
                        "\n"
                        r"Choose an option to continue: [prompt.choices]\[1/2/3][/] ",
                    ).strip()
                    if answer == "1":
                        self.api_set_django_project(djp)
                    elif answer == "2":
                        pass
                    else:
                        raise UserCancelError()
                elif (
                    "y"
                    != c.input(
                        f"Missing WSGI file `{wsgi_relative}`. "
                        "Without this your app will not deploy correctly. "
                        r"Continue anyways? [prompt.choices]\[y/N][/] ",
                    ).lower()
                ):
                    raise UserCancelError()

        # Check settings file.
        settings = p / self.django_project / "settings" / "prod.py"
        if self.env == Env.STAGING:
            settings = p / self.django_project / "settings" / "staging.py"
        settings_relative = settings.relative_to(p)
        # If settings file does not exist, offer to create it.
        if not settings.is_file():
            LOGGER.warning("Settings file does not exist! %s", settings)
            if c:
                answer = c.input(
                    f"Missing settings file `{settings_relative}`. "
                    "Without this your app will not deploy correctly.\n"
                    "[prompt.choices]1[/]) Create recommended settings.\n"
                    "[prompt.choices]2[/]) Continue anyways.\n"
                    "[prompt.choices]3[/]) Cancel and quit.\n"
                    "\n"
                    r"Choose an option to continue: [prompt.choices]\[1/2/3][/] ",
                ).strip()
                if answer == "1":
                    self.local_fix_django_settings(p)
                elif answer == "2":
                    pass
                else:
                    raise UserCancelError()
        # If settings file is missing some common strings from our recommended
        # settings, it may be incorrect, so offer to fix it.
        elif (
            "VIRTUAL_HOST" not in settings.read_text()
            or "DB_HOST" not in settings.read_text()
        ):
            LOGGER.warning("Settings file may be incorrect. %s", settings)
            if (
                c
                and "y"
                == c.input(
                    f"Settings file `{settings_relative}` may be incorrectly "
                    r"configured. Correct it? [prompt.choices]\[y/N][/] "
                ).lower()
            ):
                self.local_fix_django_settings(p)

    def local_fix_django_settings(self, p: Path) -> None:
        """
        Rewrites and/or creates Django settings file at:
        ``p``/{django_project}/settings/{env}.py
        """
        settings = p / self.django_project / "settings.py"
        settings_dir = p / self.django_project / "settings"
        settings_base = settings_dir / "base.py"
        settings_env = settings_dir / "prod.py"
        if self.env == Env.STAGING:
            settings_env = settings_dir / "staging.py"

        # If we don't have a settings.py or a settings/base.py, give up.
        if not (settings.is_file() or settings_base.is_file()):
            raise FileNotFoundError(
                "Could not find a Django settings file. "
                f"Does the folder contain a Django project named "
                f"`{self.django_project}`?"
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

        # Create settings/{env}.py
        if not settings_env.exists():
            settings_env.write_text(get_template("settings-top.py.txt"))
        # If settings/{env}.py does not look correct, append our recommended.
        settings_str = settings_env.read_text()
        if not re.findall(
            r"os\.environ\[\s*[\'\"]VIRTUAL_HOST[\'\"]\s*\]",
            settings_str,
        ):
            settings_str += "\n"
            settings_str += get_template("settings.py.txt")
        if not re.findall(
            r"os\.environ\[\s*[\'\"]DB_HOST[\'\"]\s*\]",
            settings_str,
        ):
            settings_str += "\n"
            settings_str += get_template(
                f"settings-{self.database.db_type}.py.txt"
            )
        if (
            self.app_type in [AppType.CODEREDCMS, AppType.WAGTAIL]
            and "WAGTAILADMIN_BASE_URL" not in settings_str
        ):
            settings_str += "\n"
            settings_str += get_template("settings-wagtail.py.txt")
        LOGGER.info("Writing to %s", settings_env)
        settings_env.write_text(settings_str)

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
    data: dict = None,
    timeout: int = None,
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
    data: dict = None,
    ok: List[int] = [200, 201],
) -> Tuple[int, Dict[str, Any]]:
    """
    Calls CodeRed Cloud API and returns a tuple of:
    (HTTP status code, dict of returned JSON).

    Raises a human-readable exception if the status code is not in ``ok``.
    """
    endpoint = endpoint.lstrip("/")
    code, d = request_json(
        f"https://app.codered.cloud/{endpoint}",
        method=method,
        headers={
            "Authorization": f"Token {token}",
        },
        data=data,
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
