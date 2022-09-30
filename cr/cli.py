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

Copyright (c) 2022 CodeRed LLC.
"""
from pathlib import Path, PurePosixPath
import argparse
import logging
import sys

from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TextColumn,
)
from cr import __version__, DOCS_LINK, LOGGER, UserCancelError
from cr.api import Env, Webapp
from cr.config import config, load_config
from cr.rich_utils import (
    CONSOLE,
    CONSOLE_ERR,
    Progress,
    RichArgparseFormatter,
    osc_reset,
)
from cr.ssh import Server
from cr.utils import git_ignored, paths_to_deploy


class CustomArgumentParser(argparse.ArgumentParser):
    """
    Customizes the appearance of argparse help and output.
    """

    def __init__(self, *args, **kwargs):
        """
        Override to always use the rich formatter.
        """
        kwargs["formatter_class"] = RichArgparseFormatter
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
        CONSOLE.print("See `cr --help`.")
        self.exit(2)


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
arg_token = Arg(
    "--token",
    type=str,
    help=(
        "API token which has access to webapp. "
        "If not provided, uses the `CR_TOKEN` environment variable."
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
        # Resolve path, if provided.
        extra_configs = []
        if hasattr(args, "path"):
            args.path = args.path.expanduser().resolve()
            if args.path.is_dir():
                extra_configs = [args.path]

        # Load configs, including path if provided.
        load_config(extra_configs)

        # Get token.
        token = args.token
        if not token:
            token = config("token", args.webapp)
        if not token:
            raise Exception(
                "An API token is required.\nProvide one with --token, the "
                "`CR_TOKEN` environment variable, or the `.cr.ini` file."
            )
        return Webapp(args.webapp, token)

    @classmethod
    def run(self, args: argparse.Namespace) -> None:
        pass


class Deploy(Command):

    command = "deploy"

    help = "Upload the project to CodeRed Cloud and initiate a deployment."

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
                "Path to folder containing the source code for the website. "
                "For Django & Wagtail, this is the folder with `manage.py`. "
                "For WordPress, this is the folder with `wp-config.php`. "
                "Defaults to the current directory."
            ),
        )
        p.add_argument(
            "--no-upload",
            action="store_true",
            help=(
                "Do not upload a new website version. "
                "Re-deploys the website version already on CodeRed Cloud."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)
        if not args.no_upload:
            w.local_check_path(args.path, CONSOLE)

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
                files = paths_to_deploy(args.path, e=exclude)
                s = Server(getattr(w, f"sftp_{args.env}_domain"), w.handle, "")

                # Get credentials and connect.
                t = pbar.add_task("Connecting", total=None)

                # Generate a new SFTP password from CodeRed Cloud API.
                passwd = w.api_get_sftp_password(args.env)

                # Connect to the webapp's server.
                s.passwd = passwd
                s.connect()

                # Initiate SFTP mode.
                s.open_sftp()
                pbar.update(t, total=1, completed=1)

                # Copy files.
                s.put(files, args.path, PurePosixPath("/www"), progress=pbar)
                s.close()

            # Queue the deployment task.
            t = pbar.add_task("Deploying", total=None)
            w.api_queue_deploy(args.env)
            pbar.update(t, total=1, completed=1)


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
        w.api_queue_restart(args.env)


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
                "containing `manage.py` or `wp-config.php`. "
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
                "Defaults to `/www` which is the main directory."
            ),
        )

    @classmethod
    def run(self, args: argparse.Namespace):
        w = self.get_webapp(args)

        # If the destination is the usual "www" dir, confirm with the user.
        if args.remote == PurePosixPath("/www"):
            w.local_check_path(args.path, CONSOLE)

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
                files = paths_to_deploy(args.path, e=exclude)
            else:
                files = [args.path]
            s = Server(getattr(w, f"sftp_{args.env}_domain"), w.handle, "")

            # Get credentials and connect.
            t = pbar.add_task("Connecting", total=None)

            # Generate a new SFTP password from CodeRed Cloud API.
            passwd = w.api_get_sftp_password(args.env)

            # Connect to the webapp's server.
            s.passwd = passwd
            s.connect()

            # Initiate SFTP mode.
            s.open_sftp()
            pbar.update(t, total=1, completed=1)

            # Copy files.
            s.put(files, args.path, args.remote, progress=pbar)
            s.close()


def runcli() -> None:
    """
    Entrypoint into the command-line interface.
    """

    commands = [
        Deploy,
        Restart,
        Upload,
    ]

    # Common global args.
    globalparser = CustomArgumentParser(
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
    parser = CustomArgumentParser(
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
    commands_map = {}
    for c in commands:
        commands_map[c.command] = c
        sp = subparsers.add_parser(
            name=c.command,
            description=c.help,
            help=c.help,
            add_help=False,
            parents=[globalparser],
        )
        c.add_args(sp)

    # -- Parse and route the commands ------------------------------------------

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
        CONSOLE.print(f"{parser.prog} version {__version__}")
        return

    # No sub-command provided.
    if not args.command:
        parser.print_help()
        return

    # Give the user some immediate feedback.
    CONSOLE.print(f"{args.command.title()} {args.webapp}...")

    # Run the sub-command.
    commands_map[args.command].run(args)


def main():
    try:
        runcli()
        # check_update(CONSOLE_ERR)
    # User hit Ctrl-C or manually cancelled.
    except (KeyboardInterrupt, UserCancelError):
        LOGGER.warning("Fatal: User cancelled the operation.")
        CONSOLE_ERR.print("User cancelled the operation.")
        osc_reset(CONSOLE)
        # check_update(CONSOLE_ERR)
        sys.exit(2)
    except Exception as err:
        LOGGER.exception("Fatal: %s", err)
        CONSOLE_ERR.print("[red]Error:[/]", err)
        osc_reset(CONSOLE)
        # check_update(CONSOLE_ERR)
        sys.exit(1)


if __name__ == "__main__":
    main()
