"""The archive of a backup (tinyjukebox.backup): what it holds, how its end is recognized."""

import io
import json
import os
import re
import tarfile

import pytest

from tinyjukebox import __version__
from tinyjukebox.backup import FIRST, LAST, NAME, Backup, Folder, Incomplete, verify


@pytest.fixture
def folders(tmp_path):
    images = tmp_path / "images"
    (images / ".hidden").mkdir(parents=True)
    (images / ".hidden" / "x.jpg").write_bytes(b"hidden")
    (images / "b.jpg").write_bytes(b"image b")
    (images / "a.jpg").write_bytes(b"image a")
    (images / "half.jpg.tmp").write_bytes(b"being written")
    albums = tmp_path / "albums"
    (albums / "album").mkdir(parents=True)
    (albums / "album" / "1.mp3").write_bytes(b"audio" * 1000)
    (albums / "album" / ".2.mp3.part").write_bytes(b"being fetched")
    (albums / "album" / "@eaDir" / "1.mp3").mkdir(parents=True)
    (albums / "album" / "@eaDir" / "1.mp3" / "SYNOAUDIO_01APIC_03.jpg").write_bytes(b"of the NAS")
    (albums / ".DS_Store").write_bytes(b"of a Mac")
    (albums / "staging" / "upload").mkdir(parents=True)
    (albums / "staging" / "upload" / "1.mp3").write_bytes(b"not an album yet")
    return [Folder("images", str(images)), Folder("albums", str(albums), skip=["staging"])]


def members(content: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        return {member.name: (archive.extractfile(member) or io.BytesIO()).read() for member in archive}


def written(backup: Backup) -> bytes:
    out = io.BytesIO()
    backup.write(out)
    return out.getvalue()


def test_an_archive_begins_and_ends_with_what_it_says_about_itself(folders):
    backup = Backup("box", False, [("json/tags.json", b"{}")], folders[:1], created=1_800_000_000)
    content = written(backup)
    found = members(content)
    assert list(found) == [FIRST, "json/tags.json", "images/a.jpg", "images/b.jpg", LAST]
    assert json.loads(found[FIRST]) == {
        "format": 1, "kind": "box", "version": __version__, "created": "2027-01-15T08:00:00Z", "albums": False,
    }  # fmt: skip
    assert json.loads(found[LAST]) == {"files": 3, "bytes": 2 + 7 + 7, "skipped": []}
    assert found["images/b.jpg"] == b"image b"
    assert content[:2] == b"\x1f\x8b"  # gzip
    assert verify(io.BytesIO(content)) == {**json.loads(found[FIRST]), **json.loads(found[LAST])}


def test_with_the_albums_the_archive_is_not_compressed(folders):
    backup = Backup("library", True, [], folders)
    content = written(backup)
    assert list(members(content)) == [FIRST, "images/a.jpg", "images/b.jpg", "albums/album/1.mp3", LAST]
    assert b"audio" * 1000 in content
    assert verify(io.BytesIO(content))["albums"] is True


def test_the_name_tells_kind_time_and_whether_the_albums_are_in_it():
    plain, full = Backup("library", False, created=0), Backup("box", True, created=0)
    assert re.fullmatch(r"tinyjukebox-library-\d{8}-\d{6}\.tar\.gz", plain.filename)
    assert re.fullmatch(r"tinyjukebox-box-\d{8}-\d{6}-albums\.tar", full.filename)
    assert NAME.fullmatch(plain.filename)
    assert NAME.fullmatch(full.filename)
    assert (plain.content_type, full.content_type) == ("application/gzip", "application/x-tar")


def test_a_file_that_disappears_is_left_out_and_named(folders, monkeypatch):
    gone = [("images/gone.jpg", os.path.join(folders[0].path, "gone.jpg")), *folders[0].files()]
    monkeypatch.setattr(Folder, "files", lambda self: iter(gone))
    out = io.BytesIO()
    assert Backup("box", False, [], folders[:1]).write(out)["skipped"] == ["images/gone.jpg"]
    assert verify(io.BytesIO(out.getvalue()))["files"] == 2


@pytest.mark.parametrize("albums", [False, True])
def test_an_archive_that_was_cut_off_is_recognized(folders, albums):
    content = written(Backup("library", albums, [("data/ids.json", b"{}")], folders))
    sizes = [0, 100, len(content) // 2]
    if albums:  # not compressed: the place of the last member is known - cut in its header, and right behind it
        sizes += [content.index(LAST.encode()) + 100, content.index(LAST.encode()) + 512]
    for size in sizes:
        with pytest.raises(Incomplete):
            verify(io.BytesIO(content[:size]))


def test_a_failure_while_writing_leaves_an_archive_without_its_end(folders, tmp_path):
    class Full(io.BytesIO):
        def write(self, data):
            if self.tell() + len(data) > 3000:
                raise OSError("no space left on device")
            return super().write(data)

    out = Full()
    with pytest.raises(OSError, match="no space"):
        Backup("library", True, [], folders).write(out)
    with pytest.raises(Incomplete):
        verify(io.BytesIO(out.getvalue()))
    with pytest.raises(OSError, match=r"Not a directory|No such file"):
        Backup("library", True, [], folders).save(str(tmp_path / "missing"))


def test_what_is_no_backup_is_no_backup(tmp_path):
    other = tmp_path / "other.tar"
    with tarfile.open(other, "w") as archive:
        archive.add(__file__, "some.py")
    with pytest.raises(Incomplete, match="does not begin"):
        verify(str(other))
    (tmp_path / "text").write_text("no archive")
    with pytest.raises(Incomplete):
        verify(str(tmp_path / "text"))


def test_saved_into_a_folder_under_its_name(folders, tmp_path):
    backup = Backup("box", False, [], folders[:1])
    target, result = backup.save(str(tmp_path))
    assert target == str(tmp_path / backup.filename)
    assert verify(target)["files"] == result["files"] == 2
    assert not [name for name in os.listdir(tmp_path) if name.endswith(".part")]
