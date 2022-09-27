import logging


__version__ = "1.0.0"


DOCS_LINK = "http://codered.cloud/cli"


# Logger for this module.
LOGGER = logging.getLogger("cr")


USER_AGENT = f"CodeRed-CLI/{__version__} ({DOCS_LINK})"


class UserCancelError(Exception):
    """
    Raised when a user intentionally cancels the current operation.
    """

    pass
