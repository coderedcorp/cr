"""
Utilities to call CodeRed Cloud API.

Copyright (c) 2022 CodeRed LLC.
"""
from http.client import HTTPResponse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import enum
import json

from rich.console import Console
import certifi

from cr import __version__, DOCS_LINK, LOGGER, USER_AGENT, UserCancelError


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

    def __init__(self, handle: str, token: str):
        """
        Loads the webapp info from CodeRed Cloud API.
        """
        status, d = coderedapi(f"/api/webapps/{handle}/", "GET", token)

        self.handle: str = handle
        self.token: str = token

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

    def local_check_path(self, p: Path, c: Optional[Console]) -> None:
        """
        Validity check for a provided Path ``p``. If Console ``c`` is provided,
        ask the user to continue.

        * Checks if ``p`` exists.

        * If ``p`` is a directory, check that it appears to contain an AppType
          project. If Console ``c`` is provided, ask the user to continue.

        Raises ``FileNotFoundError`` or ``UserCancelError``.
        """
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_dir():
            is_project = False
            if self.app_type in [
                AppType.CODEREDCMS,
                AppType.DJANGO,
                AppType.WAGTAIL,
            ]:
                f = "manage.py"
            elif self.app_type == AppType.WORDPRESS:
                f = "wp-config.php"
            elif self.app_type == AppType.HTML:
                f = "index.html"

            # Check for file.
            if (p / f).exists():
                is_project = True

            # Log or display a warning if file is not found.
            if not is_project:
                LOGGER.warning(
                    "`%s` does not appear to contain a %s project.",
                    p,
                    self.app_type_name,
                )
                if (
                    c
                    and "y"
                    != c.input(
                        f"Folder `{p.name}` does not appear to contain a "
                        f"{self.app_type_name} project. "
                        r"Continue anyways? [prompt.choices]\[y/N][/] ",
                    ).lower()
                ):
                    raise UserCancelError()

    def api_get_sftp_password(self, env: Env) -> str:
        """
        Resets and retrieves the tenant's SFTP password for ``env``.
        """
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": env.value,
                "task_type": "resetpassword",
            },
        )
        if "password" in d:
            return d["password"]
        if "error" in d:
            error = d["error"]
            raise Exception(f"Host Error: {error}")
        raise Exception("SFTP password not available. Please contact support.")

    def api_queue_deploy(self, env: Env):
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": env.value,
                "task_type": "init",
            },
        )
        LOGGER.info("Task created: %s", d)

    def api_queue_restart(self, env: Env) -> None:
        status, d = coderedapi(
            "/api/tasks/",
            "POST",
            self.token,
            data={
                "webapp": self.id,
                "env": env.value,
                "task_type": "restart",
            },
        )
        LOGGER.info("Task created: %s", d)


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
) -> Tuple[int, dict]:
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
    Check if a new version is available on PyPI and print to Console ``c``.
    If this fails or takes longer than 1 second, simply ignore it.
    """
    try:
        _, pypi = request_json("https://pypi.org/pypi/{NAME}/json", timeout=2)
        if __version__ != pypi["info"]["version"]:
            if c:
                c.print(
                    f"A newer version of `cr` is availabe! See: {DOCS_LINK}"
                )
            return True
    except Exception as exc:
        LOGGER.warning("Error checking version on PyPI", exc_info=exc)
    return False
