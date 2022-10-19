import logging


VERSION = "1.3"


DOCS_LINK = "https://www.codered.cloud/cli/"


# Logger for this module.
LOGGER = logging.getLogger("cr")


USER_AGENT = f"CodeRed-CLI/{VERSION} ({DOCS_LINK})"


class UserCancelError(Exception):
    """
    Raised when a user intentionally cancels the current operation.
    """

    pass
