import logging
from pathlib import Path

import pytest

from tinyjukebox.core.logging import add_stdout_handler, create_logger, get_child, get_level_name, get_log_level


@pytest.mark.parametrize(
    ("name", "level"),
    [("debug", logging.DEBUG), ("INFO", logging.INFO), ("Warning", logging.WARNING), ("NOTSET", logging.NOTSET)],
)
def test_get_log_level(name: str, level: int) -> None:
    assert get_log_level(name) == level
    assert get_level_name(level) == name.upper()


def test_unknown_levels() -> None:
    with pytest.raises(ValueError, match="unknown"):
        get_log_level("verbose")
    with pytest.raises(ValueError, match="unknown"):
        get_level_name(15)


def test_get_child_applies_level(logger: logging.Logger) -> None:
    child = get_child(logger, "child", {"log-level": "ERROR"})
    assert child.name == "tests.core.child"
    assert child.level == logging.ERROR
    assert get_child(logger, "other", {}).level == logging.NOTSET


def test_create_logger_writes_file(tmp_path: Path) -> None:
    log_file = tmp_path / "service.log"
    log = create_logger({"log-level": "INFO", "log-file": str(log_file)}, "tests.core.create")
    log.info("hello")
    log.debug("invisible")
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "tests.core.create - INFO - hello" in content
    assert "invisible" not in content


def test_create_logger_expands_the_home_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    create_logger({"log-level": "INFO", "log-file": "~/home.log"}, "tests.core.home").info("hello")
    assert "hello" in (tmp_path / "home.log").read_text(encoding="utf-8")


def test_create_logger_adds_file_handler_once(tmp_path: Path) -> None:
    config = {"log-level": "INFO", "log-file": str(tmp_path / "a.log")}
    create_logger(config, "a")
    create_logger(config, "b")
    names = [h.name for h in logging.getLogger().handlers]
    assert names.count("tinyjukebox-file") == 1


def test_create_logger_with_rotation(tmp_path: Path) -> None:
    config = {
        "log-level": "DEBUG",
        "log-file": str(tmp_path / "r.log"),
        "log-rotation": {"maxbytes": 100, "backupcount": 2},
    }
    log = create_logger(config, "tests.core.rotation")
    for i in range(20):
        log.debug("message %d", i)
    assert (tmp_path / "r.log.1").exists()
    assert not (tmp_path / "r.log.3").exists()


def test_add_stdout_handler(capsys: pytest.CaptureFixture[str]) -> None:
    add_stdout_handler("")  # quiet
    assert "tinyjukebox-stdout" not in [h.name for h in logging.getLogger().handlers]
    add_stdout_handler("WARNING")
    add_stdout_handler("WARNING")
    handlers = [h for h in logging.getLogger().handlers if h.name == "tinyjukebox-stdout"]
    assert len(handlers) == 1
