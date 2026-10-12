"""SanityCheck - consistency checks across tags, targets and activities."""

from tests.support import ONE_SECOND_MP3, TAG_ALBUM, TAG_UNKNOWN_ALBUM, make_album
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.sanitycheck import SanityCheck


def sanity_check(library, logger):
    return SanityCheck(logger, library.targets, library.tags, library.activities).sanity_check()


def test_consistent_library_passes(library, logger):
    library.tags.assign_target(TAG_UNKNOWN_ALBUM, TargetType.album, "2")
    library.activities.rebuild_activities()
    assert sanity_check(library, logger)


def test_tag_linked_to_unknown_album_fails(library, logger, caplog):
    assert not sanity_check(library, logger)
    assert "not existing album target_id '99'" in caplog.text


def test_activity_without_tag_fails(library, logger, caplog):
    library.tags.assign_target(TAG_UNKNOWN_ALBUM, TargetType.album, "2")
    library.tags.delete_tag(TAG_ALBUM)
    assert not sanity_check(library, logger)
    assert "activity json contains uids that are not in tags json" in caplog.text


def test_tag_without_activity_passes(library, logger):
    # activities are created on first playback - a linked tag without an activity is fine
    library.tags.assign_target(TAG_UNKNOWN_ALBUM, TargetType.album, "2")
    library.activities.rebuild_activities()
    library.activities.delete_activity(TAG_ALBUM)
    assert sanity_check(library, logger)


def test_broken_album_fails(library, logger, caplog):
    library.tags.assign_target(TAG_UNKNOWN_ALBUM, TargetType.album, "2")
    library.activities.rebuild_activities()
    library.targets.get_target("1").playlist = "missing.m3u"
    assert not sanity_check(library, logger)
    assert "playlist not found" in caplog.text


def test_albums_with_brackets_and_upper_case_mp3_pass(library, logger, caplog):
    """'[...]' in album/track names (no glob patterns) and '*.MP3' files are fine."""
    make_album(library.albums_folder, "Serie [Folge 1]", [("01 Intro [Live].mp3", ONE_SECOND_MP3)])
    make_album(library.albums_folder, "upper", [("01.MP3", ONE_SECOND_MP3)])
    brackets = library.targets.add_target(
        {"target-type": "album", "album-folder": "Serie [Folge 1]", "playlist": "playlist.m3u"}
    )
    upper = library.targets.add_target({"target-type": "album", "album-folder": "upper", "playlist": "playlist.m3u"})
    library.tags.assign_target(TAG_UNKNOWN_ALBUM, TargetType.album, brackets.target_id)
    library.tags.assign_target(library.tags.add_tag("4:4:4:4").tag_uid, TargetType.album, upper.target_id)
    library.activities.rebuild_activities()
    assert sanity_check(library, logger), caplog.text
    assert "not found" not in caplog.text
