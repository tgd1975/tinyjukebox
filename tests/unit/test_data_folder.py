"""Starting with one folder (--data DIR, docs/developer/configuration.md, section 1): library and box without a config
file and without a users file made by hand - for the Docker image and the package from PyPI."""

import logging
import os
import stat

import pytest

from tinyjukebox import data_folder
from tinyjukebox.core.config import validate_config
from tinyjukebox.library.schema import get_schema as library_schema
from tinyjukebox.schema.tinyjukebox import get_schema as box_schema
from tinyjukebox.web.auth import UsersFile, verify_password

LOGGER = logging.getLogger("test")


@pytest.fixture(autouse=True)
def no_variables(monkeypatch):
    for name in (data_folder.ADMIN_VARIABLE, data_folder.BOOTSTRAP_VARIABLE, data_folder.LIBRARY_VARIABLE,
                 data_folder.SLOTS_VARIABLE):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


def test_a_library_starts_with_an_empty_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(data_folder.BOOTSTRAP_VARIABLE, "bootstrap-password-of-the-test")
    monkeypatch.setenv(data_folder.SLOTS_VARIABLE, "/opt/tinyjukebox/slots")
    config = data_folder.library_config(str(tmp_path / "library"))
    assert validate_config(config, library_schema()) is None
    library = config["library"]
    assert library["data-folder"] == str(tmp_path / "library" / "data")
    assert library["backups-folder"] == str(tmp_path / "library" / "backups")
    assert library["rest-api"]["port"] == 8866
    assert library["bootstrap-password"] == "bootstrap-password-of-the-test"
    assert library["slots-folder"] == "/opt/tinyjukebox/slots"
    assert library["releases"] == {"repository": "tgd1975/tinyjukebox"}
    for name in ("data", "albums", "images", "ssl", "log"):
        assert (tmp_path / "library" / name).is_dir()
    assert stat.S_IMODE((tmp_path / "library" / "ssl").stat().st_mode) == 0o700


def test_a_file_config_yaml_changes_single_values(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "library:\n    rest-api:\n        port: 9000\n    releases:\n        quiet: 60\n"
    )
    config = data_folder.library_config(str(tmp_path))
    assert config["library"]["rest-api"]["port"] == 9000
    assert config["library"]["rest-api"]["users-file"] == str(tmp_path / "ssl" / "users.yaml")  # the rest stays
    assert config["library"]["releases"] == {"repository": "tgd1975/tinyjukebox", "quiet": 60}


def test_a_box_starts_with_an_empty_folder_and_keeps_its_data(tmp_path):
    config = data_folder.box_config(str(tmp_path))
    assert validate_config(config, box_schema()) is None
    assert config["tinyjukebox"]["rest-api"]["port"] == 8443
    tags = tmp_path / "json" / "tags.json"
    assert tags.read_text() == "{}\n"
    tags.write_text('{"04:AA": {}}\n')
    data_folder.box_config(str(tmp_path))  # the second start
    assert tags.read_text() == '{"04:AA": {}}\n'


def test_a_box_is_linked_to_its_library_at_the_first_start_only(tmp_path, monkeypatch):
    monkeypatch.setenv(data_folder.LIBRARY_VARIABLE, "https://library:8866/")
    monkeypatch.setenv(data_folder.BOOTSTRAP_VARIABLE, "bootstrap-password-of-the-test")
    data_folder.link_library(str(tmp_path), LOGGER)
    identity = tmp_path / "identity"
    assert (identity / "library").read_text() == "https://library:8866\n"
    assert (identity / "bootstrap").read_text() == "bootstrap-password-of-the-test\n"
    assert stat.S_IMODE((identity / "bootstrap").stat().st_mode) == 0o600
    # changed on the page since: the variables do not count any more
    (identity / "library").write_text("https://other:8866\n")
    data_folder.link_library(str(tmp_path), LOGGER)
    assert (identity / "library").read_text() == "https://other:8866\n"


def test_the_admin_of_the_page_from_the_environment(tmp_path, monkeypatch):
    users = str(tmp_path / "users.yaml")
    monkeypatch.setenv(data_folder.ADMIN_VARIABLE, "password-of-the-test")
    data_folder.first_admin(users, LOGGER)
    admin = UsersFile.read(users)["admin"]
    assert admin["role"] == "admin"
    assert verify_password("password-of-the-test", admin["password-hash"])
    monkeypatch.setenv(data_folder.ADMIN_VARIABLE, "another-password")
    data_folder.first_admin(users, LOGGER)  # the page has a user: nothing changes
    assert verify_password("password-of-the-test", UsersFile.read(users)["admin"]["password-hash"])


def test_without_a_terminal_the_password_is_generated_and_printed_once(tmp_path, monkeypatch, caplog, capsys):
    """On the error output (docker logs), not in the log file: the log file goes into the web page and the library."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    users = str(tmp_path / "users.yaml")
    with caplog.at_level(logging.WARNING):
        data_folder.first_admin(users, LOGGER)
    printed = capsys.readouterr().err
    password = printed.split("password ")[1].split(" ")[0]
    assert verify_password(password, UsersFile.read(users)["admin"]["password-hash"])
    [message] = [record.getMessage() for record in caplog.records]
    assert "generated password" in message
    assert password not in message


def test_a_password_from_a_file_of_docker(tmp_path, monkeypatch):
    secret = tmp_path / "admin_password"
    secret.write_text("password-from-a-file\n")
    monkeypatch.setenv(data_folder.ADMIN_VARIABLE + "_FILE", str(secret))
    monkeypatch.setenv(data_folder.ADMIN_VARIABLE, "the-file-counts-first")
    users = str(tmp_path / "users.yaml")
    data_folder.first_admin(users, LOGGER)
    assert verify_password("password-from-a-file", UsersFile.read(users)["admin"]["password-hash"])
    bootstrap = tmp_path / "bootstrap_password"
    bootstrap.write_text("bootstrap-from-a-file")
    monkeypatch.setenv(data_folder.BOOTSTRAP_VARIABLE + "_FILE", str(bootstrap))
    assert data_folder.library_config(str(tmp_path / "library"))["library"]["bootstrap-password"] == (
        "bootstrap-from-a-file"
    )
    monkeypatch.setenv(data_folder.BOOTSTRAP_VARIABLE + "_FILE", str(tmp_path / "missing"))
    with pytest.raises(SystemExit, match="cannot be read"):
        data_folder.library_config(str(tmp_path / "library"))


def test_a_short_password_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(data_folder.ADMIN_VARIABLE, "short")
    with pytest.raises(SystemExit, match="at least"):
        data_folder.first_admin(str(tmp_path / "users.yaml"), LOGGER)
    assert not os.path.exists(tmp_path / "users.yaml")


def test_the_library_takes_a_config_or_a_folder(tmp_path):
    from tinyjukebox.library.service import main

    with pytest.raises(SystemExit) as raised:
        main(["-c", str(tmp_path / "library.yaml"), "--data", str(tmp_path)])
    assert raised.value.code == 2
