"""Console helpers for UI commands: paging, yes/no questions and argument parsing."""

import shlex
import sys

__all__ = ["get_yes_no", "getch", "more", "parse"]

PAGE_SIZE = 35
_PAGER_TEXT = "   --- ENTER: next page / SPACE: next line / ESC: abort ---"
_ESC, _SPACE, _ENTER = "\x1b", " ", ("\r", "\n")


def getch() -> str:
    """Read a single character from stdin without echo (falls back to line input if stdin is not a terminal)."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line[0] if line else _ESC
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415 - platform specific

        return msvcrt.getwch()

    import termios  # noqa: PLC0415 - platform specific
    import tty  # noqa: PLC0415 - platform specific

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def more(text: str, page_size: int = PAGE_SIZE) -> None:
    """Print ``text`` page by page (only paged if stdout is a terminal)."""
    lines = text.split("\n")
    if not sys.stdout.isatty():
        print(text)
        return
    remaining = page_size
    for line in lines:
        print(line)
        remaining -= 1
        if remaining == 0:
            print(_PAGER_TEXT, end="\r", flush=True)
            while True:
                char = getch()
                if char == _ESC:
                    print(" " * len(_PAGER_TEXT), end="\r")
                    return
                if char == _SPACE:
                    remaining = 1
                    break
                if char in _ENTER:
                    remaining = page_size
                    break
            print(" " * len(_PAGER_TEXT), end="\r")


def get_yes_no() -> str:
    """Ask 'continue (y/n)' until the user answers; returns ``"y"`` or ``"n"``."""
    while True:
        print("continue (y/n):")
        char = getch().lower()
        if char in ("y", "n"):
            return char
        print(f"unexpected character '{char}'")


def parse(arg: str) -> tuple[str, ...]:
    """Split a command argument string like a shell does (quotes group words)."""
    return tuple(shlex.split(arg))
