"""Spotify in the whole box: the wiring of account, engine, state machine and page."""

from tests.support import TAG_ALBUM, TAG_UNMAPPED
from tests.unit.test_tinyjukebox import config, library, service, volumes  # noqa: F401 - fixtures
from tinyjukebox.stm.stmoperation import States


def test_a_tag_of_an_entry_without_key(service):  # noqa: F811 - the fixture
    control = service.tinyjukebox.control
    entry = control.add_spotify("spotify:album:abc", "Hörspiel")
    control.link_tag_spotify(TAG_UNMAPPED, entry["id"])
    service.rfid(TAG_UNMAPPED)
    assert service.state() == States.BROKEN_ALBUM  # no key, no player, no login
    assert control.status()["spotify"] is None
    assert control.spotify_account() == {"key": False, "connected": False, "connected_since": None,
                                         "connecting": False, "connect_result": None, "speaker": False}  # fmt: skip
    assert control.spotify_player()["state"] == "none"
    service.rfid(TAG_UNMAPPED, present=False)
    service.rfid(TAG_ALBUM)
    assert service.state() == States.PLAY_ALBUM  # albums play as before


def test_the_speaker_without_key_runs_nothing(service):  # noqa: F811 - the fixture
    control = service.tinyjukebox.control
    control.set_spotify_speaker(True)
    service.settle()
    assert not service.tinyjukebox._engine.running
    assert service.state() == States.WAIT_FOR_TAG
