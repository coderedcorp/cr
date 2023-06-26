import enum
import logging


VERSION = "1.6"


DOCS_LINK = "https://www.codered.cloud/cli/"


# Logger for this module.
LOGGER = logging.getLogger("cr")


USER_AGENT = f"CodeRed-CLI/{VERSION} ({DOCS_LINK})"


class ConfigurationError(Exception):
    """
    Raised when project does not match expected configuration.
    """

    pass


class UserCancelError(Exception):
    """
    Raised when a user intentionally cancels the current operation.
    """

    pass


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
    MYSQL = "mysql"
    POSTGRES = "postgres"

    def __str__(self):
        return self.value


class Env(enum.Enum):
    PROD = "prod"
    STAGING = "staging"

    def __str__(self):
        return self.value
