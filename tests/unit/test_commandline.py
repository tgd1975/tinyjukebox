"""Starting the service from the command line: tinyjukebox -c config.yaml [--no_gui] (separate processes)."""

import os
import re
import signal
import subprocess
import sys
import time

import pytest
import yaml

from tests.support import TAG_ALBUM, ApiClient, wait_for
from tinyjukebox import __version__
from tinyjukebox.web.auth import UsersFile, hash_password

START = [sys.executable, "-c", "from tinyjukebox.tinyjukebox import standalone; standalone()"]


def write_config(library, **changes):
    config = {
        "logger": {"log-level": "INFO", "log-file": str(library.folder / "tinyjukebox.log")},
        "tinyjukebox": {
            "albums-folder": library.albums_folder,
            "playlist-filename": "playlist.m3u",
            "albumplayer": {"pause-between-tracks": 2, "rewind-max-time": 5, "update-schedule": 5},
            "json": {"tags": library.tags_file, "targets": library.targets_file,
                     "activities": library.activities_file},
            # a mixer control that does not exist - the test must not change the volume of the machine
            "volume": {"max-volume": 80, "default-volume": 50, "volume-step": 5,
                       "mixer-control": "TinyJukeboxTestDoesNotExist"},
        },
    }  # fmt: skip
    for path, value in changes.items():
        node = config
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        if value is None:
            del node[keys[-1]]
        else:
            node[keys[-1]] = value
    filename = library.folder / "config.yaml"
    filename.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(filename)


def log(library):
    try:
        return (library.folder / "tinyjukebox.log").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


@pytest.fixture
def run_service():
    processes = []

    def run(*args):
        process = subprocess.Popen(START + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)  # fmt: skip
        processes.append(process)
        return process

    yield run
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate()


def stop(process):
    process.send_signal(signal.SIGINT)
    process.communicate(timeout=15)


def test_start_and_ctrl_c(library, run_service):
    process = run_service("-c", write_config(library), "--no_gui")
    assert wait_for(lambda: "TinyJukebox - started" in log(library), timeout=15), log(library)
    process.send_signal(signal.SIGINT)
    start = time.monotonic()
    process.communicate(timeout=15)
    assert time.monotonic() - start < 10
    assert "TinyJukebox - stopped" in log(library)
    assert "Traceback" not in log(library)


def test_config_keys_are_case_insensitive(library, run_service):
    filename = write_config(library)
    with open(filename, encoding="utf-8") as f:
        text = f.read()
    with open(filename, "w", encoding="utf-8") as f:
        f.write(text.replace("tinyjukebox:", "TinyJukebox:").replace("max-volume", "Max-Volume"))
    process = run_service("-c", filename, "--no_gui")
    assert wait_for(lambda: "TinyJukebox - started" in log(library), timeout=15), log(library)
    stop(process)


@pytest.mark.parametrize(
    "change",
    [
        {"tinyjukebox.albums-folder": None},  # required key missing
        {"tinyjukebox.volume.max-volume": "loud"},  # wrong type
        {"tinyjukebox.unknown-key": 1},  # additional key
        {"logger.log-level": "LOUD"},  # unknown log level
    ],
)
def test_invalid_config_is_rejected(library, run_service, change):
    process = run_service("-c", write_config(library, **change), "--no_gui")
    output, _ = process.communicate(timeout=30)
    assert process.returncode != 0
    assert b"Validation of config file failed" in output
    assert "TinyJukebox - started" not in log(library)


def test_missing_config_file(library, run_service):
    process = run_service("-c", str(library.folder / "does_not_exist.yaml"), "--no_gui")
    output, _ = process.communicate(timeout=30)
    assert process.returncode != 0
    assert b"does_not_exist.yaml" in output


def test_missing_json_files(library, run_service):
    """The box starts without the tags instead of not starting (docs/developer/library.md, section 4.21)."""
    os.remove(library.tags_file)
    process = run_service("-c", write_config(library), "--no_gui")
    assert wait_for(lambda: "TinyJukebox - started" in log(library), timeout=15), log(library)
    stop(process)
    assert "tags.json: neither the file nor its backup exists" in log(library)


def test_version(run_service):
    process = run_service("--version")
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 0
    assert __version__.encode() in output


def test_config_option_is_required(run_service):
    process = run_service("--no_gui")
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert b"-c/--config" in output


def test_quiet_and_verbose_are_exclusive(library, run_service):
    process = run_service("-c", write_config(library), "--no_gui", "-q", "-v")
    output, _ = process.communicate(timeout=30)
    assert process.returncode == 2
    assert b"not allowed with argument" in output
    assert "TinyJukebox - started" not in log(library)


def _rest_api_config(library):
    ssl_folder = library.folder / "ssl"
    return {"host": "127.0.0.1", "port": 0, "certificate": str(ssl_folder / "tinyjukebox.crt"),
            "private-key": str(ssl_folder / "tinyjukebox.key"),
            "users-file": str(ssl_folder / "users.yaml")}  # fmt: skip


def _api_port(library):
    match = re.search(r"rest api listening on https://[^:]+:(\d+)", log(library))
    return int(match.group(1)) if match else None


def test_no_hardware_runs_without_drivers(library, run_service):
    """--no-hardware: no device drivers, virtual volume, virtual tags - operated via the web page."""
    UsersFile.write(str(library.folder / "ssl" / "users.yaml"),
                    {"mama": {"password-hash": hash_password("geheim123"), "role": "admin"}})  # fmt: skip
    config = write_config(library, **{"tinyjukebox.rest-api": _rest_api_config(library)})
    process = run_service("-c", config, "--no_gui", "--no-hardware", "--no-audio")
    assert wait_for(lambda: _api_port(library), timeout=15), log(library)
    client = ApiClient(_api_port(library), str(library.folder / "ssl" / "tinyjukebox.crt")).login("mama", "geheim123")
    client.post("/api/rfid", {"uid": TAG_ALBUM})
    assert wait_for(
        lambda: (client.get("/api/status").json["album"] or {}).get("playback", {}).get("state") == "play"
    ), log(library)
    client.post("/api/buttons/volume-up")
    assert wait_for(lambda: client.get("/api/volume").json["volume"] == 55)
    assert "virtual volume 55%" in log(library)
    assert client.get("/api/status").json["led"] is not None
    stop(process)
    assert "Traceback" not in log(library)


def test_webserver_flags(library, run_service):
    config = write_config(library, **{"tinyjukebox.rest-api": _rest_api_config(library)})
    process = run_service("-c", config, "--no_gui", "--no-hardware", "--no-webserver")
    assert wait_for(lambda: "TinyJukebox - started" in log(library), timeout=15), log(library)
    assert _api_port(library) is None
    stop(process)

    config = write_config(library, **{"tinyjukebox.rest-api": dict(_rest_api_config(library), enabled=False)})
    os.remove(str(library.folder / "tinyjukebox.log"))
    process = run_service("-c", config, "--no_gui", "--no-hardware", "--webserver")
    assert wait_for(lambda: _api_port(library), timeout=15), log(library)
    stop(process)


def test_help_lists_the_flags(run_service):
    output, _ = run_service("--help").communicate(timeout=30)
    for flag in [b"--no-hardware", b"--no-webserver", b"--no-audio"]:
        assert flag in output
