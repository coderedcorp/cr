"""
The official CodeRed Cloud command line tool.

Exit codes:

* 0 indicates success.
* 1 indicates an error.
* 2 indicates user error or user cancelled the operation.

Logging should not be used for any user-facing output, unless debug mode is
enabled. Use logging generously to aid in customer support:

* debug: Context on the current operation.
* info: Something happened as expected.
* warning: Something is awry but not causing an error.
* error: Catching exceptions, or someting is broken.
* critical: The customer's website is most likely now broken or down
  as a result of an error. This will likely trigger a support case.

Printing output should be done through the CONSOLE object. Print minimal output
only as necessary for the user to know the program is working.

Copyright (c) 2022-2024 CodeRed LLC.
"""

import argparse
import logging
import sys
from pathlib import Path
from pathlib import PurePosixPath

from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import BarColumn
from rich.progress import MofNCompleteColumn
from rich.progress import SpinnerColumn
from rich.progress import TaskProgressColumn
from rich.progress import TextColumn
from rich.progress import TimeElapsedColumn
from rich.table import Table

from cr import DOCS_LINK
from cr import LOGGER
from cr import VERSION
from cr import UserCancelError
from cr.api import Env
from cr.api import Webapp
from cr.api import check_update
from cr.config import config
from cr.config import config_has_key
from cr.config import config_path_list
from cr.config import config_pureposixpath_list
from cr.config import load_config
from cr.config import write_token
from cr.rich_utils import CONSOLE
from cr.rich_utils import CONSOLE_ERR
from cr.rich_utils import Progress
from cr.rich_utils import RichArgparseFormatter
from cr.rich_utils import SphinxArgparseFormatter
from cr.rich_utils import osc_reset
from cr.ssh import Server
from cr.utils import check_handle
from cr.utils import git_ignored
from cr.utils import paths_to_deploy


class CustomArgumentParser(argparse.ArgumentParser):
    """
    Customizes the appearance of argparse help and output.
    """

    def __init__(self, *args, **kwargs):
        """
        Override various titles.
        """
        super().__init__(*args, **kwargs)
        self._optionals.title = "Options"
        self._positionals.title = "Required"

    def format_usage(self):
        """
        Override to add usage prefix, for consistency with ``format_help()``
        """
        formatter = self._get_formatter()
        formatter.add_usage(
            self.usage,
            self._actions,
            self._mutually_exclusive_groups,
            prefix="Usage",
        )
        return formatter.format_help()

    def format_help(self):
        """
        Override to apply slightly more customized help formatting.
        """
        formatter = self._get_formatter()

        # description
        formatter.add_text(self.description + "\n")

        # usage
        formatter.add_usage(
            self.usage,
            self._actions,
            self._mutually_exclusive_groups,
            prefix="Usage",
        )

        # positionals, optionals and user-defined groups
        for action_group in self._action_groups:
            formatter.start_section(action_group.title)
            formatter.add_text(action_group.description)
            formatter.add_arguments(action_group._group_actions)
            formatter.end_section()

        # epilog
        formatter.add_text(self.epilog)

        # Add to all help screens.
        formatter.add_text(f"Full documentation at: {DOCS_LINK}")

        # determine help from format above
        return formatter.format_help()

    def error(self, message):
        """
        Override to show a more friendly help message.
        """
        CONSOLE.print(f"{self.prog}: {message}")
        CONSOLE.print(f"See ``{self.prog} --help``.")
        self.exit(2)


class RichArgumentParser(CustomArgumentParser):
    """
    Override to use the rich formatter.
    """

    def __init__(self, *args, **kwargs):
        kwargs["formatter_class"] = RichArgparseFormatter
        super().__init__(*args, **kwargs)


class SphinxArgumentParser(CustomArgumentParser):
    """
    Tweak the output to play nice with reStructuredText formatting when used
    with ``sphinx-argparse``.
    """

    def __init__(self, *args, **kwargs):
        kwargs["formatter_class"] = SphinxArgparseFormatter
        super().__init__(*args, **kwargs)


class Arg:
    """
    Simple struct to hold re-usable add_argument() definitions.
    """

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


arg_env = Arg(
    "--env",
    type=Env,
    default=Env.PROD.value,
    choices=Env,
    help=f"The environment to act upon. Default is {Env.PROD.value}.",
)
arg_path = Arg(
    "--path",
    type=Path,
    default=Path.cwd(),
    help=(
        "Path to folder containing the source code for the website. "
        "For Django & Wagtail, this is the folder with ``manage.py``. "
        "For WordPress, this is the folder with ``wp-config.php``. "
        "Defaults to the current directory."
    ),
)
arg_token = Arg(
    "--token",
    type=str,
    help=(
        "API key which has access to webapp. "
        "If not provided, uses the ``CR_TOKEN`` environment variable."
    ),
)
arg_webapp = Arg(
    "webapp",
    type=str,
    help="The handle of the webapp.",
)


class Command:
    """
    Groups together subparser, options, and actions for a partciular command.
    """

    command = ""

    help = ""

    @classmethod
    def add_args(self, sp: argparse.ArgumentParser) -> None:
        pass

    @classmethod
    def get_webapp(self, args: argparse.Namespace) -> Webapp:
        """
        Loads the Webapp and parses common arguments.
        """
        # First check if the webapp handle could be valid.
        # If it's not, immediately throw an error.
        if not check_handle(args.webapp):
            raise Exception(
                f"Provided webapp ``{args.webapp}`` does not appear to be valid."
                f" Run ``cr {self.command} --help`` for syntax."
            )

        # Get token.
        token = self.get_token(args)
        return Webapp(args.webapp, token, args.env)

    @classmethod
    def get_token(self, args: argparse.Namespace) -> str:
        """
        Gets the token from various sources such as the command line,
        environment variables, or config.
        """
        # Resolve path, if provided.
        extra_configs = []
        if hasattr(args, "path"):
            args.path = args.path.expanduser().resolve()
            if args.path.is_dir():
                extra_configs = [args.path / ".cr.ini"]

        # Load configs, including path if provided.
        load_config(extra_configs)

        # Get token.
        token = args.token
        if not token:
            if hasattr(args, "webapp"):
                token = config("token", args.webapp)
            else:
                token = config("token")
        if not token:
            raise Exception(
                "An API key is required.\nProvide one with --token, the "
                "``CR_TOKEN`` environment variable, or the ``.cr.ini`` file."
            )
        return token

    @classmethod
    def run(self, args: argparse.Namespace) -> None:
        pass


class Login(Command):
    command = "login"

    help = "Interactively set up your first API key."

    @classmethod
    def run(self, args: argparse.Namespace):
        # Check if a config file exists, and contains a token.
        load_config()
        section, is_env = config_has_key("token")
        if section and is_env:
            CONSOLE.print(
                "API key is already in environment variable ``{section}``. "
                "Additional keys can be set in your ``~/.cr.ini`` file.\n"
                "See: https://www.codered.cloud/docs/cli/config/"
            )
        elif section:
            CONSOLE.print(
                "An API key is already set in a ``.cr.ini`` file. "
                "Additional keys can be added by editing the file.\n"
                "See: https://www.codered.cloud/docs/cli/config/"
            )
        else:
            # Prompt user to create an API token, and write it to file.
            CONSOLE.print(
                "No CodeRed Cloud API key found.\n"
                "Create one here, then enter it below.\n"
                "\n"
                "    https://app.codered.cloud/billing/api-key/\n"
            )
            token = CONSOLE.input("[prompt.choices]Enter key:[/] ")
            token = token.strip()
            path = write_token(token)
            CONSOLE.print(f"Saved to: {path}")


class List(Command):
    command = "list"

    help = (
        "List the websites on your account. "
        "If you have multiple clients, you'll need to specify "
        "a ``--token`` which has access to each client."
    )

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_token.args, **arg_token.kwargs)

    @classmethod
    def run(self, args: argparse.Namespace):
        # Check if a config file exists, and contains a token.
        token = self.get_token(args)
        webapps = []
        client = None
        # Load list of webapps and client info.
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            SpinnerColumn(),
            console=CONSOLE,
            transient=True,
        ) as pbar:
            t = pbar.add_task("Fetching list of websites", total=None)
            webapps = Webapp.all(token)
            client = webapps[0].client
            pbar.update(t, total=1, completed=1)
        # Print info.
        table = Table(box=None, pad_edge=False)
        table.add_column("Handle", no_wrap=True)
        table.add_column("Name")
        table.add_column("URL")
        for w in webapps:
            table.add_row(w.handle, w.name, w.url)
        CONSOLE.print(f"Websites owned by: {client.name}\n")
        CONSOLE.print(table)


class Deploy(Command):
    command = "deploy"

    help = "Upload the project to CodeRed Cloud and initiate a deployment."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)
        p.add_argument(*arg_path.args, **arg_path.kwargs)
        p.add_argument(
            "--no-upload",
            action="store_true",
            help=(
                "Do not upload a new website version. "
                "Re-deploys the website version already on CodeRed Cloud."
            ),
        )
        p.add_argument(
            "--skip-predeploy",
            action="store_true",
            help=(
                "Skip common pre-deployment checks. "
                "Only checks for local configuration errors."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        if not args.no_upload:
            w.local_check(args.path, CONSOLE)
            if not args.skip_predeploy:
                w.local_predeploy(args.path, CONSOLE)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=CONSOLE,
        ) as pbar:
            if not args.no_upload:
                # Get list of paths to copy.
                exclude = git_ignored(args.path)
                exclude += config_path_list("deploy_exclude", args.webapp, [])
                include = config_path_list("deploy_include", args.webapp, [])
                files = paths_to_deploy(args.path, e=exclude, i=include)
                s = Server(getattr(w, f"sftp_{args.env}_domain"), w.handle, "")

                # Get credentials and connect.
                t = pbar.add_task("Connecting", total=None)

                # Generate a new SFTP password from CodeRed Cloud API.
                passwd = w.api_get_sftp_password()

                try:
                    # Connect to the webapp's server.
                    s.passwd = passwd
                    s.connect()

                    # Initiate SFTP mode.
                    s.open_sftp()
                    pbar.update(t, total=1, completed=1)

                    # Copy files.
                    s.put(
                        files, args.path, PurePosixPath("/www"), progress=pbar
                    )
                finally:
                    s.close()

            # Queue the deployment task.
            t = pbar.add_task("Deploying", total=None)
            api_task_id = w.api_queue_deploy()
            pbar.print(
                f"[deployment queued with ID: {api_task_id}]",
                markup=False,
                style="logging.level.info",
            )

            # Poll the deployment task until it completes or times out.
            api_task = w.api_poll_task(api_task_id)
            if api_task["status"] != "completed":
                msg = "Please contact support for assistance."
                if "error" in api_task:
                    msg = api_task["error"]
                if (
                    "returned_data" in api_task
                    and "error" in api_task["returned_data"]
                ):
                    msg = api_task["returned_data"]["error"]
                raise Exception(f"Deployment encountered an error: {msg}")

            # Now poll the logs.
            pbar.print(
                "[getting deployment logs]",
                markup=False,
                style="logging.level.info",
            )
            w.api_poll_logs(pbar)
            pbar.print(
                "[connection closed]", markup=False, style="logging.level.info"
            )
            pbar.update(t, total=1, completed=1)

        CONSOLE.print(f"\nYour site is live at: {w.url}\n")


class Restart(Command):
    command = "restart"

    help = (
        "Restart the currently running website on CodeRed Cloud. "
        "No new software is installed or updated."
    )

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        w.api_queue_restart()
        CONSOLE.print(
            f"Restarting: {w.url}\nYou will receive an email when complete."
        )


class Check(Command):
    command = "check"

    help = "Check the local project for configuration errors."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)
        p.add_argument(*arg_path.args, **arg_path.kwargs)
        p.add_argument(
            "--skip-predeploy",
            action="store_true",
            help=(
                "Skip common pre-deployment checks. "
                "Only checks for local configuration errors."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        w.local_check(args.path, CONSOLE)
        if not args.skip_predeploy:
            w.local_predeploy(args.path, CONSOLE)


class Logs(Command):
    command = "logs"

    help = "Show the latest deployment logs."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=CONSOLE,
        ) as pbar:
            t = pbar.add_task("Getting logs", total=None)
            pbar.print(
                "[getting logs]", markup=False, style="logging.level.info"
            )
            w.api_poll_logs(pbar)
            pbar.print(
                "[connection closed]", markup=False, style="logging.level.info"
            )
            pbar.update(t, completed=1, total=1)


class Download(Command):
    command = "download"

    help = (
        "Download a file or folder from CodeRed Cloud. "
        "By default this will also download all media files, "
        "which may take a long time."
    )

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)
        p.add_argument(
            "--path",
            type=Path,
            default=Path.cwd(),
            help=(
                "Directory in which to download the files. "
                "Defaults to the current directory."
            ),
        )
        p.add_argument(
            "--remote",
            type=PurePosixPath,
            default=PurePosixPath("/www"),
            help=(
                "Remote directory or file on the CodeRed Cloud server to "
                "recursively download. "
                "Defaults to ``/www`` which is the main directory."
            ),
        )
        p.add_argument(
            "--exclude",
            type=PurePosixPath,
            nargs="*",
            default=[],
            help=(
                "Space separated list of directories (relative to --remote) "
                "to exclude from download. "
                "Defaults to ``--exclude cache static wp-content/cache``."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)

        exclude = args.exclude
        if not exclude:
            exclude = config_pureposixpath_list(
                "download_exclude",
                args.webapp,
                [
                    PurePosixPath("cache"),
                    PurePosixPath("static"),
                    PurePosixPath("wp-content/cache"),
                ],
            )

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=CONSOLE,
        ) as pbar:
            s = Server(getattr(w, f"sftp_{args.env}_domain"), w.handle, "")

            # Get credentials and connect.
            t = pbar.add_task("Connecting", total=None)

            # Generate a new SFTP password from CodeRed Cloud API.
            passwd = w.api_get_sftp_password()

            try:
                # Connect to the webapp's server.
                s.passwd = passwd
                s.connect()

                # Initiate SFTP mode.
                s.open_sftp()
                pbar.update(t, total=1, completed=1)

                # Copy files.
                s.get(args.remote, args.path, e=exclude, progress=pbar)
            finally:
                s.close()


class Sftp(Command):
    command = "sftp"

    help = "Show SFTP credentials and reset system password."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        if args.env == Env.STAGING and not w.staging_server:
            raise Exception(
                f"'{w.handle}' does not have a staging server. "
                "Create a staging site in the dashboard or upgrade your plan."
            )
        if args.env == Env.PROD and not w.prod_server:
            raise Exception(
                f"'{w.handle}' has not been deployed. "
                "Deploy the site first, or contact support."
            )
        host = getattr(w, f"sftp_{args.env}_domain")
        port = 22
        user = w.handle
        passwd = ""
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            SpinnerColumn(),
            console=CONSOLE,
            transient=True,
        ) as pbar:
            # Generate a new SFTP password from CodeRed Cloud API.
            t = pbar.add_task("Resetting system password", total=None)
            passwd = w.api_get_sftp_password()
            pbar.update(t, total=1, completed=1)
        CONSOLE.print(
            f"SFTP host:       {host}\n"
            f"SFTP port:       {port}\n"
            f"SFTP user:       {user}\n"
            f"System password: {passwd}"
        )
        p = Panel(
            "[logging.level.warning]NOTE:[/] "
            "Passwords are temporary and may be reset every time your "
            "website is deployed.\n"
            "SFTP guide: https://www.codered.cloud/guides/sftp/",
            border_style="cr.update_border",
        )
        CONSOLE_ERR.print(p)


class Ssh(Command):
    command = "ssh"

    help = "Show SSH credentials and reset system password."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        if not w.feature_ssh:
            raise Exception(
                f"'{w.handle}' does not have SSH enabled. "
                "Upgrade your plan to enable SSH access."
            )
        if args.env == Env.STAGING and not w.staging_server:
            raise Exception(
                f"'{w.handle}' does not have a staging server. "
                "Create a staging site in the dashboard or upgrade your plan."
            )
        if args.env == Env.PROD and not w.prod_server:
            raise Exception(
                f"'{w.handle}' has not been deployed. "
                "Deploy the site first, or contact support."
            )
        host = getattr(w, f"sftp_{args.env}_domain")
        port = 2222
        user = w.handle
        passwd = ""
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            SpinnerColumn(),
            console=CONSOLE,
            transient=True,
        ) as pbar:
            # Generate a new SFTP password from CodeRed Cloud API.
            t = pbar.add_task("Resetting system password", total=None)
            passwd = w.api_get_sftp_password()
            pbar.update(t, total=1, completed=1)
        CONSOLE.print(
            f"SSH command:     ssh -p {port} {user}@{host}\n"
            f"System password: {passwd}"
        )
        p = Panel(
            "[logging.level.warning]NOTE:[/] "
            "Passwords are temporary and may be reset every time your "
            "website is deployed.\n"
            "See SSH limitations: https://www.codered.cloud/guides/ssh/",
            border_style="cr.update_border",
        )
        CONSOLE_ERR.print(p)


class Upload(Command):
    command = "upload"

    help = "Upload a file or folder to CodeRed Cloud."

    @classmethod
    def add_args(self, p: argparse.ArgumentParser):
        p.add_argument(*arg_webapp.args, **arg_webapp.kwargs)
        p.add_argument(*arg_env.args, **arg_env.kwargs)
        p.add_argument(*arg_token.args, **arg_token.kwargs)
        p.add_argument(
            "--path",
            type=Path,
            default=Path.cwd(),
            help=(
                "Path to a file or folder to upload. "
                "If this is a file, it will be uploaded into --remote. "
                "If this is a folder, its contents will be recursively "
                "uploaded into --remote. "
                "To upload your full website, this should be the folder "
                "containing ``manage.py`` or ``wp-config.php``. "
                "Defaults to the current directory."
            ),
        )
        p.add_argument(
            "--remote",
            type=PurePosixPath,
            default=PurePosixPath("/www"),
            help=(
                "Remote directory on the CodeRed Cloud server in which to upload "
                "the file or folder. "
                "Defaults to ``/www`` which is the main directory."
            ),
        )
        p.add_argument(
            "--skip-predeploy",
            action="store_true",
            help=(
                "Skip common pre-deployment checks. "
                "Only checks for local configuration errors."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)

        # If the destination is the usual ``/www`` dir, and ``--path`` is a
        # directory, confirm with the user.
        if args.remote == PurePosixPath("/www") and args.path.is_dir():
            w.local_check(args.path, CONSOLE)
            if not args.skip_predeploy:
                w.local_predeploy(args.path, CONSOLE)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=CONSOLE,
        ) as pbar:
            # Get list of paths to copy.
            if args.path.is_dir():
                exclude = git_ignored(args.path)
                exclude += config_path_list("deploy_exclude", args.webapp, [])
                include = config_path_list("deploy_include", args.webapp, [])
                files = paths_to_deploy(args.path, e=exclude, i=include)
            else:
                files = [args.path]
            s = Server(getattr(w, f"sftp_{args.env}_domain"), w.handle, "")

            # Get credentials and connect.
            t = pbar.add_task("Connecting", total=None)

            # Generate a new SFTP password from CodeRed Cloud API.
            passwd = w.api_get_sftp_password()

            try:
                # Connect to the webapp's server.
                s.passwd = passwd
                s.connect()

                # Initiate SFTP mode.
                s.open_sftp()
                pbar.update(t, total=1, completed=1)

                # Copy files.
                s.put(files, args.path, args.remote, progress=pbar)
            finally:
                s.close()


COMMANDS = [
    Check,
    Deploy,
    Download,
    List,
    Login,
    Logs,
    Restart,
    Sftp,
    Ssh,
    Upload,
]


def make_parser(klass=SphinxArgumentParser) -> argparse.ArgumentParser:
    """
    Sets up argparse with all the subcommands and options.

    This is in a separate function for compatibility with generating docs using
    ``sphinx-argparse``. For that reason, we are also defaulting to the non-rich
    SphinxArgumentParser because the sphinx-docs are unaware of rich's meta
    formatting codes.
    """

    # Common global args.
    globalparser = klass(
        add_help=False,
    )
    globalargs = globalparser.add_argument_group("Global Options")
    globalargs.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show info about the given command.",
    )
    globalargs.add_argument(
        "--debug",
        action="store_true",
        help="Output verbose debug logging.",
    )

    # The main parser.
    parser = klass(
        prog="cr",
        description="CodeRed Cloud command line tool.",
        add_help=False,
        parents=[globalparser],
    )

    # Version.
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Print the program version and exit.",
    )

    # Add commands as subparsers.
    subparsers = parser.add_subparsers(
        title="Commands",
        dest="command",
    )
    for c in COMMANDS:
        sp = subparsers.add_parser(
            name=c.command,
            description=c.help,
            help=c.help,
            add_help=False,
            parents=[globalparser],
        )
        c.add_args(sp)

    return parser


def runcli() -> None:
    """
    Entrypoint into the command-line interface.
    """

    # -- Parse and route the commands ------------------------------------------

    commands_map = {}
    for c in COMMANDS:
        commands_map[c.command] = c

    parser = make_parser(klass=RichArgumentParser)
    args = parser.parse_args()

    # Set up logging.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.CRITICAL,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=CONSOLE,
                show_time=False,
                rich_tracebacks=True,
            )
        ],
    )

    # Version.
    if args.version:
        CONSOLE.print(f"{parser.prog} version {VERSION}")
        return

    # No sub-command provided.
    if not args.command:
        parser.print_help()
        return

    # Run the sub-command.
    commands_map[args.command].run(args)


def main():
    try:
        runcli()
        check_update(CONSOLE_ERR)
    # User hit Ctrl-C or manually cancelled.
    except (KeyboardInterrupt, UserCancelError):
        LOGGER.warning("Fatal: User cancelled the operation.")
        CONSOLE_ERR.print("User cancelled the operation.")
        osc_reset(CONSOLE)
        check_update(CONSOLE_ERR)
        sys.exit(2)
    except Exception as err:
        LOGGER.exception("Fatal: %s", err)
        CONSOLE_ERR.print("[logging.level.error]Error:[/]", err)
        osc_reset(CONSOLE)
        check_update(CONSOLE_ERR)
        sys.exit(1)


if __name__ == "__main__":
    main()
