"""
Utilities to format program output with `rich`.

Copyright (c) 2022 CodeRed LLC.
"""
import argparse
from typing import Generator
from typing import Iterable
from typing import List
from typing import Optional

from rich.console import Console
from rich.console import Group
from rich.console import RenderableType
from rich.console import WINDOWS
from rich.highlighter import RegexHighlighter
from rich.measure import measure_renderables
from rich.padding import Padding
from rich.progress import Progress as _Progress
from rich.progress import TaskID
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# List of colors: https://rich.readthedocs.io/en/latest/appendix/colors.html
#
# Generally, try to stick to "system" colors (i.e. 0-16) which will use the
# user's terminal theme.
RICH_THEME = Theme(
    {
        # Override defaults.
        "bar.complete": "cyan",
        "bar.finished": "cyan",
        "bar.pulse": "deep_sky_blue1",
        "logging.level.warning": "yellow",
        "progress.download": "bright_green",
        "progress.percentage": "bright_magenta",
        "prompt.choices": "yellow",
        # Custom.
        "cr.argparse_args": "cyan",
        "cr.argparse_groups": "default",
        "cr.argparse_help": "default",
        "cr.argparse_text": "default",
        "cr.code": "bright_magenta",
        "cr.progress_print": "bright_black",
        "cr.update_border": "bright_black",
    }
)


class CustomHighlighter(RegexHighlighter):
    """
    Highlights word formats in output.
    """

    base_style = "cr."
    highlights = [
        # Highlight --words-with-dashes as cr.argparse_args.
        r"\W(?P<argparse_args>-{1,2}[\w]+[\w-]*)",
        # Highlight text in backticks as cr.code
        r"`(?P<code>[^`]*)`",
    ]


CONSOLE = Console(highlighter=CustomHighlighter(), theme=RICH_THEME)
CONSOLE_ERR = Console(
    highlighter=CustomHighlighter(), theme=RICH_THEME, stderr=True
)


def osc_reset(console):
    """
    Provides extra support for Windows Terminal and ConEmu to set operating
    system codes (OSC) for progress indicators.
    """
    if WINDOWS and not console.legacy_windows:
        console.print("\x1b]9;4;0;0\x1b\\", end="")


def osc_indeterminate(console):
    """
    Provides extra support for Windows Terminal and ConEmu to set operating
    system codes (OSC) for progress indicators.
    """
    if WINDOWS and not console.legacy_windows:
        console.print("\x1b]9;4;3;0\x1b\\", end="")


def osc_progress(console, percent: int):
    """
    Provides extra support for Windows Terminal and ConEmu to set operating
    system codes (OSC) for progress indicators.

    :param int percent: 0 to 100
    """
    if WINDOWS and not console.legacy_windows:
        console.print(f"\x1b]9;4;1;{percent}\x1b\\", end="")


class Progress(_Progress):
    """
    Provides extra support for Windows Terminal and ConEmu to set operating
    system codes (OSC) for progress indicators.
    """

    def add_task(self, *args, **kwargs) -> TaskID:
        """
        Override to initiate OSC progress indicator.
        """
        tid = super().add_task(*args, **kwargs)

        # Only continue on supported systems.
        if not WINDOWS or self.console.legacy_windows:
            return tid

        task = self._tasks[tid]

        # Set indeterminite status if no total.
        if task.total is None:
            osc_indeterminate(self.console)

        # If we have a total, show the percentage.
        elif task.total:
            osc_progress(self.console, int(task.percentage))

        return tid

    def update(self, task_id: TaskID, *args, **kwargs) -> None:
        """
        Override to reset OSC progress, or update the percentage.
        """
        super().update(task_id, *args, **kwargs)

        # Only continue on supported systems.
        if not WINDOWS or self.console.legacy_windows:
            return

        task = self._tasks[task_id]

        # Reset if completed.
        if task.total and task.completed >= task.total:
            osc_reset(self.console)

        # If we have a total, show the percentage.
        elif task.total:
            osc_progress(self.console, int(task.percentage))


class RichArgparseFormatter(
    argparse.RawTextHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """
    An argparse HelpFormatter class that renders using rich.

    Originally sourced from:

        https://github.com/hamdanal/rich-argparse

        Author: Ali Hamdan (ali.hamdan.dev@gmail.com).

        Copyright (C) 2022.

        Permission is granted to use, copy, and modify this code in any manner as
        long as this copyright message and disclaimer remain in the source code.
        There is no warranty. Try to use the code for the greater good.

    Modifications and changes:

        Copyright (c) 2022 CodeRed LLC.
    """

    @staticmethod
    def group_name_formatter(s: str) -> str:
        return f"{s.title()}:"

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 38,
        width: Optional[int] = None,
    ) -> None:
        super().__init__(prog, indent_increment, max_help_position, width)
        # Add a `renderables` array to store our custom rendering for the
        # relevant sections.
        self._root_section.renderables = []  # type: ignore[attr-defined]

    @property
    def renderables(self) -> List[RenderableType]:
        return self._current_section.renderables  # type: ignore[attr-defined]

    @property
    def _table(self) -> Table:
        return self._current_section.table  # type: ignore[attr-defined]

    def _pad(self, renderable: RenderableType) -> Padding:
        return Padding(renderable, pad=(0, 0, 0, self._current_indent))

    def _format_action_invocation(self, action: argparse.Action) -> str:
        if not action.option_strings or action.nargs == 0:
            action_invocation = super()._format_action_invocation(action)
        else:
            # The default format: `-s ARGS, --long-option ARGS` is very ugly
            # with long option names so I change it to `-s, --long-option ARG`
            # similar to click.
            default = self._get_default_metavar_for_optional(action)
            args_string = self._format_args(action, default)
            action_invocation = (
                f"{', '.join(action.option_strings)} {args_string}"
            )

        if self._current_section != self._root_section:
            col1 = self._pad(action_invocation)
            col2 = self._expand_help(action) if action.help else ""
            self._table.add_row(col1, col2)

        return action_invocation

    def add_text(self, text: Optional[str]) -> None:
        super().add_text(text)

        if text is not argparse.SUPPRESS and text is not None:
            if "%(prog)" in text:
                text = text % {"prog": self._prog}
            self.renderables.append(
                self._pad(Text.from_markup(text, style="cr.argparse_text"))
            )

    def add_argument(self, action):
        """
        Override to not double indent subactions.
        """
        if action.help is not argparse.SUPPRESS:
            # find all invocations
            get_invocation = self._format_action_invocation
            # Ignore the initial invocation of subparser and get subactions
            # instead.
            if isinstance(action, argparse._SubParsersAction):
                invocations = []
                for subaction in action._get_subactions():
                    invocations.append(get_invocation(subaction))
            else:
                invocations = [get_invocation(action)]

            # update the maximum item length
            invocation_length = max(map(len, invocations))
            action_length = invocation_length + self._current_indent
            self._action_max_length = max(
                self._action_max_length, action_length
            )

            # add the item to the list
            self._add_item(self._format_action, [action])

    def add_usage(
        self,
        usage: Optional[str],
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: Optional[str] = None,
    ) -> None:
        # Do not pass prefix along... instead format it as a group title.
        super().add_usage(usage, actions, groups, prefix)

        if usage is not argparse.SUPPRESS:
            usage_text = self._format_usage(usage, actions, groups, prefix)
            # Append prefix as group title.
            title = type(self).group_name_formatter(prefix or "")
            self.renderables.append(
                f"[cr.argparse_groups]{title}[/] {usage_text}\n"
            )

    def _format_usage(
        self,
        usage: Optional[str],
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._ArgumentGroup],
        prefix: Optional[str] = None,
    ):
        """
        Override to generate a shorter / more concise version of the usage.
        The usage generated by argparse can be unwieldy.
        """
        # If usage is specified, use that.
        if usage is not None:
            usage = usage % dict(prog=self._prog)

        # If no optionals or positionals are available, usage is just prog.
        elif usage is None and not actions:
            usage = "%(prog)s" % dict(prog=self._prog)

        # If optionals and positionals are available, calculate a
        # condensed/simplified version compared to what argparse normally
        # generates.
        elif usage is None:
            usage = "%(prog)s" % dict(prog=self._prog)

            # Split optionals from positionals.
            optionals = []
            positionals = []
            for action in actions:
                if action.option_strings:
                    optionals.append(action)
                else:
                    positionals.append(action)

            # If options are available, represent them as `[options]`
            if optionals:
                usage += r" \[[cr.argparse_args]options[/]]"
            # If positionals are required, render each.
            for p in positionals:
                metavar = p.metavar or self._get_default_metavar_for_positional(
                    p
                )
                usage += f" [cr.argparse_args]{metavar}[/]"

        return usage

    def start_section(self, heading: Optional[str]) -> None:
        super().start_section(
            heading
        )  # sets self._current_section to child section

        self._current_section.renderables = []  # type: ignore[attr-defined]
        self._current_section.table = Table(  # type: ignore[attr-defined]
            box=None,
            pad_edge=False,
            show_header=False,
            show_edge=False,
            highlight=True,
        )
        self._table.add_column(
            style="cr.argparse_args",
            max_width=self._max_help_position,
            overflow="fold",
        )
        self._table.add_column(
            style="cr.argparse_help",
            min_width=self._width - self._max_help_position,
        )

    def end_section(self) -> None:
        if self.renderables or self._table.row_count:
            title = type(self).group_name_formatter(
                self._current_section.heading or ""
            )
            self.renderables.insert(0, f"[cr.argparse_groups]{title}")
            if self._table.row_count:
                self._table.add_row(end_section=True)
                self.renderables.append(self._table)
        # group the renderables of the section
        group = Group(*self.renderables)

        super().end_section()  # sets self._current_section to parent section
        # append the group to the parent section
        self.renderables.append(group)

    def format_help(self) -> str:
        out = super().format_help()

        # Handle ArgumentParser.add_subparsers() call to get the program name
        all_items = self._root_section.items
        if len(all_items) == 1:
            func, args = all_items[0]
            # If we are currently executing "format_usage()", return the program
            # name instead of printing it. Otherwise it will print `: cr`
            # randomly at the top of the usage section.
            if func == self._format_usage and not list(args)[-1]:
                return out

        renderables = Group(*self.renderables)

        def iter_tables(group: Group) -> Generator[Table, None, None]:
            for renderable in group.renderables:
                if isinstance(renderable, Table):
                    yield renderable
                elif isinstance(renderable, Group):
                    yield from iter_tables(renderable)

        col1_width = 0
        for table in iter_tables(
            renderables
        ):  # compute a unified width of all tables
            cells = table.columns[0].cells
            table_col1_width = measure_renderables(
                CONSOLE, CONSOLE.options, tuple(cells)
            ).maximum
            col1_width = max(col1_width, table_col1_width)
        col1_width = min(col1_width, self._max_help_position)
        col2_width = self._width - col1_width
        for table in iter_tables(renderables):  # apply the unified width
            table.columns[0].width = col1_width
            table.columns[1].width = col2_width

        CONSOLE.print(renderables, highlight=True)
        return ""
