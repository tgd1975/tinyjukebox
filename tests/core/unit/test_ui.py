import io
import logging
import threading

import pytest

from tests.support import wait_for
from tinyjukebox.core.ui import UI, tools


def make_ui(logger: logging.Logger, stop_service: list[str], stopping: threading.Event) -> UI:
    return UI("intro", "(test) ", lambda: stop_service.append("stopped"), stopping, logger)


class TestUI:
    def test_add_command(self, logger: logging.Logger) -> None:
        ui = make_ui(logger, [], threading.Event())
        calls: list[str] = []

        def hello(arg: str) -> None:
            """hello - say hello"""
            calls.append(arg)

        ui.add_command("hello", hello)
        assert "do_hello" in ui.get_names()
        ui.onecmd("hello world")
        assert calls == ["world"]
        with pytest.raises(ValueError, match="already defined"):
            ui.add_command("hello", hello)
        with pytest.raises(TypeError):
            ui.add_command("other", "not callable")  # type: ignore[arg-type]

    def test_commands_are_per_instance(self, logger: logging.Logger) -> None:
        first = UI("", "", print, threading.Event(), logger, {"x": print})
        second = UI("", "", print, threading.Event(), logger, {"x": repr})
        assert first.do_x is print  # type: ignore[attr-defined]
        assert second.do_x is repr  # type: ignore[attr-defined]

    def test_failing_command(self, logger: logging.Logger, capsys: pytest.CaptureFixture[str]) -> None:
        def fail(arg: str) -> None:
            raise RuntimeError("broken")

        ui = UI("", "", print, threading.Event(), logger, {"fail": fail})
        assert ui.onecmd("fail") is False
        assert "Caught exception: broken" in capsys.readouterr().out

    @pytest.mark.parametrize("command", ["exit", "e", "q", "EOF"])
    def test_exit_commands(self, logger: logging.Logger, command: str) -> None:
        assert make_ui(logger, [], threading.Event()).onecmd(command) is True

    def test_empty_line_does_nothing(self, logger: logging.Logger) -> None:
        assert make_ui(logger, [], threading.Event()).emptyline() is False

    def test_leaving_the_loop_stops_the_service(self, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("exit\n"))
        stopped: list[str] = []
        ui = make_ui(logger, stopped, threading.Event())
        ui.use_rawinput = False
        ui.start()
        assert wait_for(lambda: stopped == ["stopped"])
        ui.stop()

    def test_stop_while_service_is_stopping(self, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))  # EOF
        stopped: list[str] = []
        stopping = threading.Event()
        stopping.set()
        ui = make_ui(logger, stopped, stopping)
        ui.use_rawinput = False
        ui.start()
        ui.stop()
        assert stopped == []

    def test_the_end_of_an_input_without_terminal_does_not_stop_the_service(
        self, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """E.g. started by a script with stdin /dev/null - the service would stop right after its start."""
        monkeypatch.setattr("sys.stdin", io.StringIO(""))  # EOF, no terminal
        stopped: list[str] = []
        ui = make_ui(logger, stopped, threading.Event())
        ui.use_rawinput = False
        ui.start()
        assert wait_for(lambda: not ui._thread.is_alive())
        assert stopped == []
        assert "the console ends, the service goes on" in caplog.text

    def test_ctrl_d_on_a_terminal_stops_the_service(
        self, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        terminal = io.StringIO("")
        terminal.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", terminal)
        stopped: list[str] = []
        ui = make_ui(logger, stopped, threading.Event())
        ui.use_rawinput = False
        ui.start()
        assert wait_for(lambda: stopped == ["stopped"])

    def test_a_line_after_the_stop_is_not_executed(self, logger: logging.Logger) -> None:
        """input() cannot be interrupted: the loop ends with the next line - which must not run any more."""
        typed = threading.Event()

        class Keyboard(io.StringIO):
            def readline(self, size: int | None = -1, /) -> str:  # type: ignore[override]
                typed.wait(5)
                return "hello\n"

        calls: list[str] = []
        stopped: list[str] = []
        ui = UI("", "", lambda: stopped.append("stopped"), threading.Event(), logger, {"hello": calls.append})
        ui.stdin = Keyboard()
        ui.use_rawinput = False
        ui.start()
        ui.stop()  # still waits for the line
        typed.set()
        assert wait_for(lambda: not ui._thread.is_alive())
        assert calls == []
        assert stopped == []


class TestTools:
    def test_parse(self) -> None:
        assert tools.parse('a "b c" d') == ("a", "b c", "d")

    def test_more_without_terminal(self, capsys: pytest.CaptureFixture[str]) -> None:
        tools.more("line1\nline2")
        assert capsys.readouterr().out == "line1\nline2\n"

    def test_more_pages(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        keys = iter(["x", " ", "\r", "\x1b"])
        monkeypatch.setattr(tools, "getch", lambda: next(keys))
        tools.more("\n".join(f"line{i}" for i in range(10)), page_size=3)
        output = capsys.readouterr().out
        assert "line4" in output  # page, then single line (SPACE), then page (ENTER)
        assert "line7" not in output  # ESC aborted

    def test_get_yes_no(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        keys = iter(["x", "Y"])
        monkeypatch.setattr(tools, "getch", lambda: next(keys))
        assert tools.get_yes_no() == "y"
        assert "unexpected character 'x'" in capsys.readouterr().out

    def test_getch_without_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
        assert tools.getch() == "y"
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert tools.getch() == "\x1b"
