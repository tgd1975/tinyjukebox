import logging

import pytest

from tinyjukebox.core.threads import LoggerThread


def test_runs_target() -> None:
    result: list[int] = []
    thread = LoggerThread(target=result.append, args=(1,))
    thread.start()
    thread.join()
    assert result == [1]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_logs_uncaught_exception(logger: logging.Logger, caplog: pytest.LogCaptureFixture) -> None:
    def fail() -> None:
        raise ValueError("boom")

    thread = LoggerThread(target=fail, name="failing", logger=logger)
    thread.start()
    thread.join()
    assert "uncaught exception in thread 'failing'" in caplog.text
    assert "ValueError: boom" in caplog.text
