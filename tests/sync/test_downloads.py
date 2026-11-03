"""A file that a box fetches from the library is continued where it broke off (docs/developer/library.md, section 4.8):
by the box itself, and by curl in the slot script."""

import shutil
import subprocess
from urllib.parse import quote

import pytest

from tinyjukebox.sync import INTERFACE
from tinyjukebox.sync.link import V


def _album_file(bed, box):
    """(path of the request, content) of the largest file of an album of the library."""
    album = next(k for k, v in bed.catalog("target").items() if v["target-type"] == "album")
    entry = max(bed.library.store.manifest(album)["files"], key=lambda item: item["size"])
    with open(bed.library.store.album_file(album, entry["name"]), "rb") as infile:
        return f"{V}/albums/{album}/files/{quote(entry['name'], safe='')}", infile.read()


@pytest.mark.parametrize("have", [0.5, 1.0, 0.0], ids=["half", "all", "nothing"])
def test_the_box_continues_a_file_where_it_broke_off(bed, tmp_path, have):
    box = bed.box("box1")
    assert box.synced()
    path, content = _album_file(bed, box)
    part = tmp_path / "file.part"
    part.write_bytes(content[: int(len(content) * have)])
    answer = box.sync.link.client.request("GET", path, sink=str(part))
    assert part.read_bytes() == content
    assert answer.status == (206 if 0 < have < 1 else 200)


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
@pytest.mark.parametrize("have", [0.5, 1.0], ids=["half", "all"])
def test_curl_continues_a_file_as_the_slot_script_does(bed, tmp_path, have):
    box = bed.box("box1")
    assert box.synced()
    path, content = _album_file(bed, box)
    part = tmp_path / "file.part"
    part.write_bytes(content[: int(len(content) * have)])
    key = (box.identity / "key").read_text().strip()
    result = subprocess.run(
        ["curl", "-fsS", "-k", "-C", "-", "-o", str(part), "-H", f"Authorization: Box {box.sync.data.box_id}:{key}",
         "-H", f"X-Box-Interface: {INTERFACE}", f"{bed.url}{path}"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert part.read_bytes() == content
