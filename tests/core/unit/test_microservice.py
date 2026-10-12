import logging
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.core.support import ExampleService
from tests.support import SyncPubSub, wait_for
from tinyjukebox.core.microservice import AbstractMicroservice
from tinyjukebox.core.pubsub import LocalQueue


def test_start_stop_with_shared_client(config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger) -> None:
    service = ExampleService(config, sync_pubsub, logger)
    assert service.is_stopped
    assert not service.is_started
    assert service.config == config["test"]
    assert service.version == "2.0"

    service.start()
    assert service.is_started
    assert not service.is_stopped
    assert sync_pubsub.messages("/test/start") == ["start"]

    service.stop()
    assert service.is_stopped
    assert not service.is_started
    assert sync_pubsub.messages("/test/stop") == ["stop"]
    assert sync_pubsub.is_connected.is_set()  # a shared client is not disconnected by the service


def test_own_client_is_created_and_disconnected(config: dict[str, Any], log_file: str) -> None:
    service = ExampleService(config)
    client = service._pubsub_client
    assert isinstance(client, LocalQueue)
    service.start()
    assert client.is_connected.is_set()
    service.stop()
    assert client.is_disconnected.is_set()
    assert Path(log_file).exists()


def test_stop_via_message_and_restart(config: dict[str, Any], local_queue: LocalQueue, logger: logging.Logger) -> None:
    service = ExampleService(config, local_queue, logger)
    for _ in range(2):
        service.start()
        local_queue.publish("/test/stop-me", "")
        assert service.stopped_by_message.wait(5)
        assert wait_for(lambda: service.is_stopped)
        service.stopped_by_message.clear()


def test_run_blocks_until_stopped(config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger) -> None:
    service = ExampleService(config, sync_pubsub, logger)
    runner = threading.Thread(target=service.run)
    runner.start()
    assert wait_for(lambda: service.is_started)
    service.asyncstop()
    runner.join(5)
    assert not runner.is_alive()


def test_run_stops_on_keyboard_interrupt(
    config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ExampleService(config, sync_pubsub, logger)

    def interrupt(timeout: float) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(service._is_stopped, "wait", interrupt)
    service.run()
    assert not service.is_started


def test_run_stops_on_keyboard_interrupt_at_the_end_of_the_start(
    config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop signal arrives when the service has started but ``start()`` has not returned yet."""
    service = ExampleService(config, sync_pubsub, logger)
    start = service.start

    def start_and_interrupt() -> None:
        start()
        raise KeyboardInterrupt

    monkeypatch.setattr(service, "start", start_and_interrupt)
    service.run()
    assert not service.is_started
    assert service.is_stopped


def test_run_does_not_stop_twice_if_the_interrupt_broke_the_start_off(
    config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ExampleService(config, sync_pubsub, logger)
    stops = []
    monkeypatch.setattr(service, "_start", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(service, "_stop", lambda: stops.append(True))
    service.run()
    assert stops == [True]  # by the start that failed, not again by run()
    assert service.is_stopped


def test_asyncstart(config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger) -> None:
    service = ExampleService(config, sync_pubsub, logger)
    service.asyncstart()
    assert wait_for(lambda: service.is_started)
    service.stop()


class TestHooks:
    class Bare(AbstractMicroservice):
        pass

    def test_abstract_hooks_raise(
        self, config: dict[str, Any], sync_pubsub: SyncPubSub, logger: logging.Logger
    ) -> None:
        service = self.Bare(config, "test", sync_pubsub, logger, no_gui=True)
        for hook in (service._start, service._stop):
            with pytest.raises(NotImplementedError):
                hook()
        for class_hook in (self.Bare._get_schema, self.Bare._get_description):
            with pytest.raises(NotImplementedError):
                class_hook()
        assert self.Bare._get_schema_definitions() == {}


class TestCommandLine:
    def test_arguments(self) -> None:
        parse = ExampleService._argument_parser().parse_args
        arguments = parse(["-c", "c.yaml"])
        assert (arguments.config, arguments.quiet, arguments.verbose, arguments.no_gui) == (
            "c.yaml",
            False,
            False,
            False,
        )
        arguments = parse(["--config", "c.yaml", "-q", "--no_gui"])
        assert (arguments.quiet, arguments.no_gui) == (True, True)
        assert parse(["-c", "c.yaml", "-v"]).verbose

    def test_quiet_and_verbose_are_exclusive(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            ExampleService._argument_parser().parse_args(["-c", "c.yaml", "-q", "-v"])
        assert "not allowed with argument" in capsys.readouterr().err

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            ExampleService._argument_parser().parse_args(["--version"])
        assert "2.0" in capsys.readouterr().out

    def test_read_config_validates(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("logger:\n  log-level: INFO\n  log-file: x.log\n")
        with pytest.raises(ValueError, match="'test' is a required property"):
            ExampleService._read_config(config_file)

    def test_dump_schema(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        target = tmp_path / "schema.json"
        ExampleService.dump_schema(target)
        ExampleService.dump_schema(target)
        assert capsys.readouterr().out.count("updated") == 1


class TestUICommands:
    @pytest.fixture
    def output(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        texts: list[str] = []
        monkeypatch.setattr("tinyjukebox.core.microservice.more", texts.append)
        return texts

    def test_commands(self, config: dict[str, Any], output: list[str]) -> None:
        config["test"]["password"] = "secret"
        service = ExampleService(config)
        assert set(service._ui_commands) == {"pubsub_stats", "show_config", "debug_threads"}
        service._ui_commands["show_config"]("")
        service._ui_commands["pubsub_stats"]("")
        service._ui_commands["debug_threads"]("")
        assert "secret" not in output[0]
        assert "*****" in output[0]
        assert "'connected': False" in output[1]
        assert "active threads" in output[2]

    def test_duplicate_command_is_ignored(self, config: dict[str, Any], sync_pubsub: SyncPubSub, logger: Any) -> None:
        service = ExampleService(config, sync_pubsub, logger)
        original = service._ui_commands["debug_threads"]
        service._add_ui_command("debug_threads", print)
        assert service._ui_commands["debug_threads"] == original

    def test_interactive_ui_is_started_and_stopped(
        self, config: dict[str, Any], sync_pubsub: SyncPubSub, logger: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[Any] = []
        monkeypatch.setattr("tinyjukebox.core.ui.cli.UI.start", lambda self: started.append(self))  # noqa: PLW0108
        monkeypatch.setattr("tinyjukebox.core.ui.cli.UI.stop", lambda self: started.remove(self))  # noqa: PLW0108
        service = ExampleService(config, sync_pubsub, logger, no_gui=False)
        service.start()
        assert len(started) == 1
        assert hasattr(started[0], "do_debug_threads")
        service._add_ui_command("late", print)  # commands added after start reach the running UI
        assert hasattr(started[0], "do_late")
        service.stop()
        assert started == []
