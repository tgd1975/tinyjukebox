"""The images folder: the covers of the albums and of the radio stations (tinyjukebox.images)."""

import os
import re

import pytest

from tinyjukebox.errors import BadRequest, NotFound
from tinyjukebox.images import ALBUM_PREFIX, Images, is_cover, take_over

JPEG = b"\xff\xd8\xff" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def test_a_cover_gets_a_name_of_its_own_and_is_read_by_it(tmp_path):
    told = []
    images = Images(str(tmp_path / "images"))
    images.listen(lambda: told.append(1))
    assert images.names() == []  # the folder is created with the first cover
    name = images.add(PNG)
    assert name.startswith("station-")
    assert name.endswith(".png")
    assert images.names() == [name]
    assert images.image(name) == (PNG, "image/png")
    with pytest.raises(BadRequest):
        images.add(b"GIF89a")
    images.delete(name)
    assert not images.has(name)
    assert len(told) == 2
    # the cover of an album
    cover = images.add(JPEG, ALBUM_PREFIX)
    assert re.fullmatch(r"album-[0-9a-f]{8}\.jpg", cover)
    assert images.names() == [cover]
    assert images.image(cover) == (JPEG, "image/jpeg")
    assert os.listdir(images.folder) == [cover]  # nothing is left of writing it


def test_only_covers_count(tmp_path):
    """Whatever else lies in the folder - a card image of a tag from before 0.27, an image that
    tinyjukebox-import-covers read, a file of somebody - is not there for the page and the synchronization."""
    (tmp_path / "album-14.jpg").write_bytes(JPEG)
    (tmp_path / "station-notes.txt").write_text("no image")
    (tmp_path / "station-fm4.jpg").write_bytes(JPEG)
    (tmp_path / "album-00ff00ff.png").write_bytes(PNG)
    (tmp_path / ".album-00ff00aa.png.tmp").write_bytes(PNG)
    images = Images(str(tmp_path))
    assert images.names() == ["album-00ff00ff.png", "station-fm4.jpg"]
    assert is_cover("station-a.WEBP")
    assert is_cover("album-1a2b3c4d.webp")
    assert not is_cover("../station-a.jpg")
    assert not is_cover("../album-1a2b3c4d.jpg")
    assert not is_cover("album-1a2b3c4d-1.jpg")
    assert not is_cover("album-short.jpg")
    assert not is_cover("album-1a2b3c4d.txt")
    with pytest.raises(NotFound):
        images.image("album-14.jpg")
    with pytest.raises(NotFound):
        images.delete("../images/station-fm4.jpg")


def test_the_covers_are_taken_over_from_the_folder_of_the_card_images(tmp_path):
    old, new = tmp_path / "cards", tmp_path / "images"
    assert take_over(str(old), str(new)) == 0  # a box that never had the folder
    assert not new.exists()
    old.mkdir()
    for name in ("station-fm4.jpg", "station-oe1.png", "album-14.jpg", "cards.json"):
        (old / name).write_bytes(JPEG)
    new.mkdir()
    (new / "station-oe1.png").write_bytes(PNG)  # the one that is there already stays
    assert take_over(str(old), str(new)) == 1
    assert sorted(os.listdir(new)) == ["station-fm4.jpg", "station-oe1.png"]
    assert (new / "station-oe1.png").read_bytes() == PNG
    assert sorted(os.listdir(old)) == ["album-14.jpg", "cards.json", "station-oe1.png"]  # nothing is deleted
    assert take_over(str(old), str(new)) == 0
