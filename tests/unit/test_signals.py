"""SIGTERM (systemd) and SIGHUP (screen -X quit) stop the service cleanly - like Ctrl-C."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tests.support import box_config, wait_for
from tinyjukebox.messages import LED, Color, LedPattern


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_signal_stops_the_service_cleanly(library, tmp_path: Path, signum: signal.Signals):
    config = box_config(library)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config))
    log_file = Path(config["logger"]["log-file"])
    process = subprocess.Popen(
        [sys.executable, "-m", "tinyjukebox.tinyjukebox", "-c", str(config_file), "--no-hardware", "--no-webserver",
         "--no_gui", "-q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,  # own process group: SIGINT is not ignored like in a background job
    )  # fmt: skip
    try:
        assert wait_for(lambda: log_file.exists() and "TinyJukebox - started" in log_file.read_text(), timeout=20)
        start = time.monotonic()
        os.kill(process.pid, signum)
        if signum == signal.SIGTERM:
            # a box with slots: systemd and the start script may both send the signal - the stop is not interrupted
            for _ in range(20):
                time.sleep(0.005)
                if process.poll() is None:
                    os.kill(process.pid, signum)
        _, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert time.monotonic() - start < 10
    log = log_file.read_text()
    assert "TinyJukebox - stopped" in log, stderr
    assert f"'{LED}', message '{LedPattern.static(Color.RED)}'" in log  # led switched to red on shutdown
    assert process.returncode == 0, stderr
