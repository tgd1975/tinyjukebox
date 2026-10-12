"""JsonData: loading with backup fallback, schema validation and (re)writing of the regular and the backup file."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support import DATA, file_id
from tinyjukebox.data.jsondata import JsonData

SCENARIOS = DATA / "jsondata"
FILENAME = "filename.json"
BACKUP_FILENAME = "filename.json_"
GOOD_DATA = {"type": "radio", "id": "id"}

Files = tuple[str, str]  # regular and backup file


@pytest.fixture(scope="module")
def schema():
    return json.loads((SCENARIOS / "schema.json").read_text())


@pytest.fixture
def prepare(tmp_path: Path) -> Callable[[str], Files]:
    """Copy the files of a scenario (regular and/or backup file) to ``tmp_path``."""

    def _prepare(scenario: str) -> Files:
        for name in (FILENAME, BACKUP_FILENAME):
            if (SCENARIOS / scenario / name).exists():
                shutil.copy(SCENARIOS / scenario / name, tmp_path / name)
        return str(tmp_path / FILENAME), str(tmp_path / BACKUP_FILENAME)

    return _prepare


@pytest.fixture
def files(prepare: Callable[[str], Files]) -> Files:
    """Regular and backup file of the 'good' scenario."""
    return prepare("good")


def same_content(filename, backup_filename):
    try:
        with open(filename, encoding="utf-8") as f1, open(backup_filename, encoding="utf-8") as f2:
            return f1.read() == f2.read()
    except FileNotFoundError:
        return False


def test_init_good(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    assert jd.data == GOOD_DATA


@pytest.mark.parametrize("scenario", ["wrong_regular", "empty_regular", "missing_regular"])
def test_init_broken_regular_uses_backup(prepare, schema, logger, scenario):
    filename, backup_filename = prepare(scenario)
    assert not same_content(filename, backup_filename)
    jd = JsonData(filename, backup_filename, schema, logger)
    assert jd.data == GOOD_DATA
    assert same_content(filename, backup_filename)  # regular file rewritten with the content of the backup


@pytest.mark.parametrize("scenario", ["miss_regular_ety_backup", "miss_regular_miss_backup"])
def test_init_missing_regular_and_no_usable_backup(prepare, schema, logger, scenario):
    filename, backup_filename = prepare(scenario)
    with pytest.raises(FileNotFoundError):
        JsonData(filename, backup_filename, schema, logger)


def test_update_new_data(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    assert jd.data["id"] == "id"
    jd.data["id"] = "test"
    assert jd.data == {"type": "radio", "id": "test"}

    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data()
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) != prev_bfn

    assert JsonData(*files, schema, logger).data["id"] == "test"


def test_store_same_data_is_skipped(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data()
    assert file_id(files[0]) == prev_fn
    assert file_id(files[1]) == prev_bfn


def test_force_store_same_data(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data(force_write=True)
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) != prev_bfn


def test_force_store_same_data_no_backup(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data(force_write=True, skip_backup=True)
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) == prev_bfn


def test_update_new_data_no_backup(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    assert jd.data["id"] == "id"
    jd.data["id"] = "test"
    assert jd.data == {"type": "radio", "id": "test"}

    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data(skip_backup=True)
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) == prev_bfn

    assert JsonData(*files, schema, logger).data["id"] == "test"


def test_force_update_new_data(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    assert jd.data["id"] == "id"
    jd.data["id"] = "test"
    assert jd.data == {"type": "radio", "id": "test"}

    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data(force_write=True)
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) != prev_bfn

    assert JsonData(*files, schema, logger).data["id"] == "test"


def test_force_update_new_data_explicit_backup(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    assert jd.data["id"] == "id"
    jd.data["id"] = "test"
    assert jd.data == {"type": "radio", "id": "test"}

    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data(force_write=True, skip_backup=False)
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) != prev_bfn

    assert JsonData(*files, schema, logger).data["id"] == "test"


def test_update_schema_good(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    jd.data["type"] = "album"
    assert jd.data == {"type": "album", "id": "id"}
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store_data()
    assert file_id(files[0]) != prev_fn
    assert file_id(files[1]) != prev_bfn


def test_update_schema_bad_enum_value(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    jd.data["type"] = "test"
    assert jd.data == {"type": "test", "id": "id"}
    with pytest.raises(ValueError, match="does not match the schema"):
        jd.store_data()


def test_update_schema_bad_additional_property(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    jd.data["x"] = "y"
    assert jd.data == {"type": "radio", "id": "id", "x": "y"}
    with pytest.raises(ValueError, match="does not match the schema"):
        jd.store_data()


def test_store_replaces_data_and_keeps_previous_file_as_backup(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    previous_text = Path(files[0]).read_text(encoding="utf-8")
    jd.store({"type": "album", "id": "7"})
    assert jd.data == {"type": "album", "id": "7"}
    assert json.loads(Path(files[0]).read_text(encoding="utf-8")) == {"type": "album", "id": "7"}
    assert Path(files[1]).read_text(encoding="utf-8") == previous_text
    assert not Path(files[0] + ".tmp").exists()
    assert JsonData(*files, schema, logger).data == {"type": "album", "id": "7"}


def test_store_unchanged_data_is_skipped(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    jd.store(dict(GOOD_DATA))
    assert file_id(files[0]) == prev_fn
    assert file_id(files[1]) == prev_bfn


def test_store_invalid_data_keeps_previous_data(files: Files, schema, logger):
    jd = JsonData(*files, schema, logger)
    prev_fn, prev_bfn = file_id(files[0]), file_id(files[1])
    with pytest.raises(ValueError, match="does not match the schema"):
        jd.store({"type": "test", "id": "id"})
    assert jd.data == GOOD_DATA
    assert file_id(files[0]) == prev_fn
    assert file_id(files[1]) == prev_bfn
    jd.store_data()  # the restored data is still the stored data - nothing to write
    assert file_id(files[0]) == prev_fn
