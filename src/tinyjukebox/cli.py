"""Commands of the interactive command line (core UI) - text front end of :class:`~tinyjukebox.control.BoxControl`.

Output goes to the console with ``print``/``more``: this is the user interface, not logging.
"""

import functools
import os
from collections.abc import Callable
from typing import Any

import tinyjukebox.core.ui.tools
from tinyjukebox.control import BoxControl
from tinyjukebox.errors import ApiError
from tinyjukebox.sync import acting_as

__all__ = ["CLI"]

AddCommand = Callable[[str, Callable[[str], None]], None]


def _confirm() -> bool:
    return tinyjukebox.core.ui.tools.get_yes_no() == "y"


def _target_text(tag: dict[str, Any]) -> str:
    """What the tag is linked to, for the console."""
    if tag["radio_id"] is not None:
        return f"radio station {tag['radio_id']}"
    if tag["spotify_id"] is not None:
        return f"entry of Spotify {tag['spotify_id']}"
    return f"album {tag['album_id'] or '-'}"


def _at_the_console(command: Callable[[str], None]) -> Callable[[str], None]:
    """What the command changes is noted as entered on the console (tinyjukebox.sync)."""

    @functools.wraps(command)
    def wrapped(args: str) -> None:
        with acting_as("console"):
            command(args)

    return wrapped


class CLI:
    def __init__(self, add_ui_command: AddCommand, control: BoxControl) -> None:
        self._control = control
        for name in [
            "list_tags",
            "list_albums",
            "list_activities",
            "unmapped_tags",
            "unmapped_albums",
            "list_folders",
            "unadded_folders",
            "sanity_checks",
            "rebuild_activity_json",
            "set_tag_name",
            "link_tag_album",
            "link_current_tag",
            "unlink_tag",
            "remove_tag",
            "track_prev",
            "track_next",
            "track_pause",
            "track_play",
            "restart_album",
            "set_volume",
            "get_volume",
            "get_volume_limits",
            "set_volume_limits",
            "bluetooth_info",
            "bluetooth_pair",
            "bluetooth_unpair",
            "album_info",
            "system_state",
            "add_album",
            "add_new_albums",
            "set_album_title",
            "set_album_cover",
            "list_radios",
            "add_radio",
            "set_radio_name",
            "set_radio_url",
            "set_radio_cover",
            "remove_radio",
            "test_radio",
            "stop_radio_test",
            "link_tag_radio",
            "link_current_tag_radio",
            "list_spotify",
            "add_spotify",
            "set_spotify_name",
            "set_spotify_link",
            "set_spotify_cover",
            "remove_spotify",
            "link_tag_spotify",
            "link_current_tag_spotify",
            "library_info",
            "set_library",
            "unlink_library",
            "system_info",
            "backup",
            "restore",
        ]:
            add_ui_command(name, _at_the_console(getattr(self, "cmd_" + name)))

    # --- lists -------------------------------------------------------------------------------------------------

    def cmd_list_tags(self, args: str) -> None:
        """list_tags - list of all tags"""
        output = "list of all tags:\n"
        for tag in self._control.list_tags():
            if tag["radio_id"]:
                target = f"radio: {tag['radio_id']}"
            elif tag["spotify_id"]:
                target = f"spotify: {tag['spotify_id']}"
            else:
                target = f"album: {tag['album_id'] or '-'}"
            output += f"[{tag['uid']}] '{tag['name']}' {target} created: {tag['created']}\n"
        tinyjukebox.core.ui.tools.more(output)

    def _album_lines(self, unmapped_only: bool) -> str:
        return "".join(
            f"[{a['id']}] {self._control.album_text(a['id'])}\n" for a in self._control.list_albums(unmapped_only)
        )

    def cmd_list_albums(self, args: str) -> None:
        """list_albums - list of all albums"""
        tinyjukebox.core.ui.tools.more("list of all albums:\n" + self._album_lines(False))

    def cmd_list_activities(self, args: str) -> None:
        """list_activities - list of all activities"""
        output = "list of all activities:\n"
        for a in self._control.list_activities():
            output += (
                f"[{a['tag_uid']}] album: {a['album_id']}, track id: {a['track_id']}, time: {a['track_time']}, "
                f"finished: {a['finished']}\n"
            )
        tinyjukebox.core.ui.tools.more(output)

    def cmd_unmapped_tags(self, args: str) -> None:
        """unmapped_tags - list of all tags that are not linked to an album or radio station"""
        output = "list of all unmapped tags:\n"
        for tag in self._control.list_tags(unmapped_only=True):
            output += f"[{tag['uid']}] '{tag['name']}' created: {tag['created']}\n"
        tinyjukebox.core.ui.tools.more(output)

    def cmd_unmapped_albums(self, args: str) -> None:
        """unmapped_albums - list of all albums that are not linked to a tag"""
        tinyjukebox.core.ui.tools.more("list of all unmapped albums:\n" + self._album_lines(True))

    def cmd_list_folders(self, args: str) -> None:
        """list_folders - list of all folders with a playlist in albums-folder (added and not yet added albums)"""
        output = "list of all album folders:\n" + "".join(f"{folder}\n" for folder in self._control.album_folders())
        tinyjukebox.core.ui.tools.more(output)

    def cmd_unadded_folders(self, args: str) -> None:
        """unadded_folders - list of all folders with a playlist in albums-folder that are not added as album"""
        folders = self._control.new_album_folders()
        tinyjukebox.core.ui.tools.more("list of all unadded folders:\n" + "".join(f"{folder}\n" for folder in folders))

    # --- maintenance -------------------------------------------------------------------------------------------

    def cmd_sanity_checks(self, args: str) -> None:
        """sanity_checks - check tags, albums and activities"""
        print("running sanity checks:")
        result = self._control.sanity_checks()
        for message in result["messages"]:
            print(f" - {message}")
        print(f"success: {result['passed']}")

    def cmd_rebuild_activity_json(self, args: str) -> None:
        """rebuild_activity_json - rebuild the activities: all albums start from the beginning."""
        print("rebuilding activity json - all album states would be reset to start.")
        if not _confirm():
            print("canceled.")
            return
        self._control.rebuild_activities()
        print("finished rebuilding activity json.")

    # --- tags --------------------------------------------------------------------------------------------------

    def cmd_set_tag_name(self, args: str) -> None:
        """set_tag_name - sets the name of a tag: SET_TAG_NAME UID this is the new name"""
        words = list(tinyjukebox.core.ui.tools.parse(args))
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected at least 2)")
            return
        uid, name = words[0], " ".join(words[1:])
        try:
            self._control.set_tag_name(uid, name)
        except ApiError:
            print(f"unknown uid '{uid}'")
            return
        print(f"[{uid}] updated name to '{name}'")

    def _link_tag_album(self, tag_uid: str, album_id: str) -> None:
        try:
            tag = self._control.get_tag(tag_uid)
        except ApiError:
            print(f"unknown tag-uid {tag_uid}.")
            return
        try:
            album = self._control.album_text(album_id)
        except ApiError:
            print(f"unknown album-id {album_id}.")
            return
        print(f"linking tag '{tag['uid']}' ('{tag['name']}') with album '{album}'.")
        if not _confirm():
            print("canceled.")
            return
        result = self._control.link_tag_album(tag_uid, album_id)
        print(f"link result: tag {result['uid']} -> album {result['album_id']}")
        if result["present"]:
            print("the tag is on the box - the album starts now.")

    def cmd_link_tag_album(self, args: str) -> None:
        """link_tag_album - links a tag with an album.

        LINK_TAG_ALBUM TAG-UID ALBUM-ID - link a specific tag with an album
        LINK_TAG_ALBUM AUTO ALBUM-ID - link the unlinked tag on the box with an album
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 2:
            print(f"wrong number of arguments (got {len(words)}, expected 2)")
            return
        tag_uid = self._tag_uid_or_auto(words[0])
        if tag_uid is not None:
            self._link_tag_album(tag_uid, words[1])

    def _tag_uid_or_auto(self, word: str) -> str | None:
        """``word`` or - for AUTO - the unlinked tag on the box (None if there is none)."""
        if word.upper() != "AUTO":
            return word
        tag_uid = self._control.current_unlinked_tag()
        if tag_uid is None:
            print("No unlinked tag currently present.")
        else:
            print(f"Using currently present unlinked tag: {tag_uid}")
        return tag_uid

    def cmd_link_current_tag(self, args: str) -> None:
        """link_current_tag - links the unlinked tag on the box with an album.

        LINK_CURRENT_TAG - show the albums that are not linked yet
        LINK_CURRENT_TAG ALBUM-ID - link the unlinked tag on the box with the album
        """
        current_uid = self._control.current_unlinked_tag()
        if current_uid is None:
            print("No unlinked tag currently present.")
            return
        print(f"Using currently present unlinked tag: {current_uid}")
        words = tinyjukebox.core.ui.tools.parse(args)
        if not words:
            self.cmd_unmapped_albums("")
        elif len(words) == 1:
            self._link_tag_album(current_uid, words[0])
        else:
            print(f"wrong number of arguments (got {len(words)}, expected 0 or 1)")

    def cmd_unlink_tag(self, args: str) -> None:
        """unlink_tag - removes the link of a tag to its album or radio station (the position is kept).

        UNLINK_TAG TAG-UID - unlink a specific tag
        UNLINK_TAG CURRENT - unlink the tag on the box (the playback stops)
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        uid = "current" if words[0].upper() == "CURRENT" else words[0]
        try:
            tag = self._control.get_tag(uid)
        except ApiError as e:
            print(e.message)
            return
        if tag["album_id"] is None and tag["radio_id"] is None and tag["spotify_id"] is None:
            print(f"tag '{tag['uid']}' is not linked.")
            return
        target = _target_text(tag)
        print(f"unlinking tag '{tag['uid']}' ('{tag['name']}') from {target}.")
        if not _confirm():
            print("canceled.")
            return
        result = self._control.unlink_tag(str(tag["uid"]))
        print(f"unlinked tag {result['uid']}.")
        if result["present"]:
            print("the tag is on the box - the playback stops.")

    def cmd_remove_tag(self, args: str) -> None:
        """remove_tag - remove a tag with its activity and card: REMOVE_TAG UID"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        tag_uid = words[0]
        try:
            tag = self._control.get_tag(tag_uid)
        except ApiError:
            print(f"unknown tag-uid {tag_uid}.")
            return
        print(f"removing tag {tag['uid']} ('{tag['name']}', album: {tag['album_id']}) from system.")
        if not _confirm():
            print("canceled.")
            return
        self._control.remove_tag(tag_uid)
        print(f"removed tag uid: {tag_uid}")

    # --- player / volume ---------------------------------------------------------------------------------------

    def _album_command(self, text: str, command: Callable[[], None]) -> None:
        try:
            command()
        except ApiError:
            print("currently, no album is active.")
            return
        print(text)

    def cmd_track_prev(self, args: str) -> None:
        """track_prev - play the previous track"""
        self._album_command("playing previous track", self._control.track_prev)

    def cmd_track_next(self, args: str) -> None:
        """track_next - play the next track"""
        self._album_command("playing next track", self._control.track_next)

    def cmd_track_pause(self, args: str) -> None:
        """track_pause - pause the album or radio station (the tag stays on the box)"""
        self._album_command("paused", self._control.pause)

    def cmd_track_play(self, args: str) -> None:
        """track_play - end the pause"""
        self._album_command("playing", self._control.play)

    def cmd_restart_album(self, args: str) -> None:
        """restart_album - play the album from the beginning"""
        self._album_command("restarting album", self._control.restart_album)

    def cmd_set_volume(self, args: str) -> None:
        """set_volume - set the volume in %: SET_VOLUME [0-100]"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        try:
            target_volume = float(words[0])
        except ValueError:
            print("argument must be a valid number.")
            return
        if not 0 <= target_volume <= 100:
            print(f"target volume must be 0-100. got {target_volume}.")
            return
        volume = self._control.set_volume(target_volume)
        print(f"volume of the {volume['output']} set to {volume['volume']} (max-volume: {volume['max_volume']}).")

    def cmd_get_volume(self, args: str) -> None:
        """get_volume - current volume in % (of the headset while it is connected)"""
        volume = self._control.get_volume()
        print(f"volume of the {volume['output']} is set to: {volume['volume']:0.0f}")

    def cmd_get_volume_limits(self, args: str) -> None:
        """get_volume_limits - start value and maximum of the volume of speaker and headphones"""
        for name, profile in self._control.get_volume()["profiles"].items():
            print(f"{name}: start {profile['default_volume']:0.0f}, maximum {profile['max_volume']:0.0f}")

    def cmd_set_volume_limits(self, args: str) -> None:
        """set_volume_limits - set start value and maximum of a volume in % (kept over restarts):
        SET_VOLUME_LIMITS speaker|headphones START MAXIMUM"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 3:
            print(f"wrong number of arguments (got {len(words)}, expected 3)")
            return
        try:
            start, maximum = float(words[1]), float(words[2])
        except ValueError:
            print("start value and maximum must be valid numbers.")
            return
        try:
            self._control.set_volume_limits(words[0].lower(), start, maximum)
        except ApiError as e:
            print(e.message)
            return
        print(f"{words[0].lower()}: start {start:0.0f}, maximum {maximum:0.0f}")

    # --- library and version -----------------------------------------------------------------------------------

    def cmd_library_info(self, args: str) -> None:
        """library_info - the synchronization with the library: address, connection, changes not confirmed yet"""
        try:
            info = self._control.library_info()
        except ApiError as e:
            print(e.message)
            return
        if not info["library"]:
            print(f"box {info['box']}: no library - this box works for itself.")
            return
        state = (
            "from the next start of the box"
            if not info["attached"]
            else "connected"
            if info["connected"]
            else f"not reachable ({info['alone']})"
        )
        print(f"box {info['box']} '{info['name'] or ''}', library {info['library']}: {state}, fit: {info['fit']}")
        print(f"changes not confirmed yet: {info['outbox']}, new targets not settled: {len(info['unsettled'])}")

    def cmd_set_library(self, args: str) -> None:
        """set_library - address of the library, for the first contact with the bootstrap password; takes effect
        with the next start: SET_LIBRARY https://HOST:PORT [BOOTSTRAP-PASSWORD]"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) not in (1, 2):
            print(f"wrong number of arguments (got {len(words)}, expected 1 or 2)")
            return
        try:
            self._control.set_library(words[0], words[1] if len(words) == 2 else None)
            print("done. A box that had no library works with it from its next start.")
        except ApiError as e:
            print(e.message)

    def cmd_unlink_library(self, args: str) -> None:
        """unlink_library - leave the library: the box keeps what is on its card and works for itself. Changes not
        confirmed yet never reach the library, albums that are not on the card cannot be fetched any more:
        UNLINK_LIBRARY YES"""
        try:
            info = self._control.library_info()
            if tinyjukebox.core.ui.tools.parse(args) != ("YES",):
                print(
                    f"changes not confirmed yet: {info['outbox']}, albums not completely on the card: {info['absent']}"
                )
                print("they are lost for the library and for this box. To unlink: UNLINK_LIBRARY YES")
                return
            self._control.unlink_library()
            print("done. This box works for itself.")
        except ApiError as e:
            print(e.message)

    def cmd_system_info(self, args: str) -> None:
        """system_info - version of the box software, slots and update mode (changes: tinyjukebox-slot in a shell)"""
        info = self._control.system_info()
        if not info["available"]:
            print(f"version {info['version']} - this box was set up without slots.")
            return
        print(f"version {info['version']}, active slot: {info['active']}, update mode: {info['mode']}")
        for name, slot in info["slots"].items():
            print(f"  {name}: {slot['version']} {slot['state']}")
        if info["problem"]:
            print(f"problem: {info['problem']}")

    # --- bluetooth headset -------------------------------------------------------------------------------------

    def cmd_bluetooth_info(self, args: str) -> None:
        """bluetooth_info - the paired headset: connected, battery, volume, start value and maximum"""
        info = self._control.bluetooth_info()
        if not info["available"]:
            print("bluetooth is not available.")
            return
        limits = f"start {info['default_volume']:0.0f}, maximum {info['max_volume']:0.0f}"
        if info["pairing"]:
            print("searching for a headset in pairing mode ...")
        elif info["pairing_result"] is not None:
            print(f"last search: {info['pairing_result']}")
        device = info["device"]
        if device is None:
            print(f"no headset is paired (volume: {limits}).")
            return
        battery = "-" if info["battery"] is None else f"{info['battery']} %"
        volume = "-" if info["volume"] is None else f"{info['volume']:0.0f}"
        print(
            f"[{device['address']}] '{device['name']}' connected: {info['connected']}, battery: {battery}, "
            f"volume: {volume} ({limits})"
        )

    def cmd_bluetooth_pair(self, args: str) -> None:
        """bluetooth_pair - search a headset in pairing mode and pair it (replaces the paired one)"""
        try:
            self._control.bluetooth_pair()
        except ApiError as e:
            print(e.message)
            return
        print("searching for a headset in pairing mode for up to a minute - bluetooth_info shows the result.")

    def cmd_bluetooth_unpair(self, args: str) -> None:
        """bluetooth_unpair - remove the paired headset"""
        device = self._control.bluetooth_info()["device"]
        if device is None:
            print("no headset is paired.")
            return
        print(f"removing the pairing of '{device['name']}' ({device['address']}).")
        if not _confirm():
            print("canceled.")
            return
        try:
            self._control.bluetooth_unpair()
        except ApiError as e:
            print(e.message)
            return
        print("pairing removed.")

    def cmd_album_info(self, args: str) -> None:
        """album_info - information on the playing album or radio station"""
        info = self._control.album_info()
        if info is None:
            print("currently, no album is active.")
            return
        if info.get("target_type") == "radio":
            error = f" error: {info['error']}" if info["error"] else ""
            test = " (test)" if info["target_id"] == "test" else ""
            print(
                f"[{info['target_id']}] radio{test} '{info['name']}' url: {info['url']} state: {info['state']}{error}"
            )
            return
        output = (
            f"[{info['target_id']}] album_folder: {info['album_folder']} playlist: {info['playlist']} "
            f"state: {info['state']}\n"
        )
        track = info["track"]
        if track["track_id"] is not None:
            output += (
                f"track: [{track['track_id']}] '{track['filename']}' @ {track['pos']:.1f}s / {track['length']:.1f}s\n"
            )
        output += "tracks:\n" + "".join(f" - {t}\n" for t in info["tracks"])
        tinyjukebox.core.ui.tools.more(output)

    def cmd_system_state(self, args: str) -> None:
        """system_state - state of the box"""
        state = self._control.state()
        print(f"system state is: {getattr(state, 'value', state)}.")

    # --- albums ------------------------------------------------------------------------------------------------

    def cmd_add_album(self, args: str) -> None:
        """add_album - add the album in a folder (relative to albums-folder) with its title.

        ADD_ALBUM FOLDER this is the title - a folder with blanks in quotes: ADD_ALBUM "my folder" this is the title
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if not words:
            print("folder must be provided.")
            return
        folder, title = words[0], " ".join(words[1:])
        if not title:
            print("title must be provided: ADD_ALBUM FOLDER this is the title (a folder with blanks in quotes).")
            return
        print(f"adding album '{folder}' with the title '{title}' to system.")
        if not _confirm():
            print("canceled.")
            return
        try:
            album = self._control.add_album(folder, title)
        except ApiError as e:
            print(f"adding album failed: {e.message}")
            return
        print(f"added album with id '{album['id']}'.")

    def cmd_add_new_albums(self, args: str) -> None:
        """add_new_albums - add all folders with a playlist in albums-folder that are not yet known. Every album gets
        the title that is shown: the album that its first track names, otherwise the name of the folder. Change it
        with SET_ALBUM_TITLE."""
        new_albums = self._control.new_albums()
        if not new_albums:
            print("no new albums found.")
            return
        print(f"found {len(new_albums)} new albums:")
        for album in new_albums:
            print(f" - {album['folder']}  ->  title: {album['title']}")
        if not _confirm():
            print("canceled.")
            return
        result = self._control.add_new_albums({album["folder"]: album["title"] for album in new_albums})
        for album in result["added"]:
            print(f"added [{album['id']}] {album['folder']} - '{album['title']}'")
        for skipped in result["skipped"]:
            print(f"skipped '{skipped['folder']}': {skipped['error']}")

    def cmd_set_album_title(self, args: str) -> None:
        """set_album_title - sets the title of an album: SET_ALBUM_TITLE ALBUM-ID this is the new title"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected at least 2)")
            return
        try:
            album = self._control.update_album(words[0], " ".join(words[1:]))
        except ApiError as e:
            print(f"changing album failed: {e.message}")
            return
        print(f"[{album['id']}] updated title to '{album['title']}'")

    @staticmethod
    def _id_and_image(args: str) -> tuple[str, bytes] | None:
        """The arguments ``ID FILE``: the id and the content of the file - None (and a message) if they are wrong."""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected at least 2)")
            return None
        filename = os.path.expanduser(" ".join(words[1:]))
        try:
            with open(filename, "rb") as infile:
                return words[0], infile.read()
        except OSError as e:
            print(f"cannot read '{filename}': {e.strerror}")
            return None

    def cmd_set_album_cover(self, args: str) -> None:
        """set_album_cover - makes an image file the cover of an album: SET_ALBUM_COVER ALBUM-ID FILE"""
        given = self._id_and_image(args)
        if given is None:
            return
        try:
            album = self._control.set_cover(*given)
        except ApiError as e:
            print(f"changing album failed: {e.message}")
            return
        print(f"[{album['id']}] '{album['title']}' has a new cover.")

    def cmd_backup(self, args: str) -> None:
        """backup - writes a backup of the data into a folder, with ALBUMS also the albums: BACKUP FOLDER [ALBUMS]"""
        words = args.split()
        albums = len(words) > 1 and words[-1].lower() == "albums"
        folder = os.path.expanduser(args.strip()[: -len("albums")].strip() if albums else args.strip())
        if not folder:
            print("expected: BACKUP FOLDER [ALBUMS]")
            return
        if not os.path.isdir(folder):
            print(f"'{folder}' is no folder.")
            return
        try:
            target, result = self._control.backup(albums).save(folder)
        except OSError as e:
            print(f"the backup failed: {e}")
            return
        print(f"backup written to {target}: {result['files']} files, {result['bytes']} bytes.")

    def cmd_restore(self, args: str) -> None:
        """restore - puts a backup back: RESTORE FILE unpacks it beside the data, RESTORE CONFIRM replaces the data by
        it and starts the box again, RESTORE CANCEL leaves the data as it is"""
        restore = self._control.restore
        word = args.strip()
        if restore is None:
            print("no restore here.")
            return
        if not word:
            print("expected: RESTORE FILE, then RESTORE CONFIRM or RESTORE CANCEL")
            return
        try:
            if word.lower() == "confirm":
                restore.confirm()
                print("the data is replaced: the box starts again.")
            elif word.lower() == "cancel":
                restore.discard()
                print("the data stays as it is.")
            else:
                about = restore.stage(os.path.expanduser(word))
                what = (
                    "with the albums - all albums are deleted and replaced" if about["albums"] else "without the albums"
                )
                print(
                    f"backup of {about['created']} (version {about['version']}, {about['files']} files), {what}.\n"
                    "RESTORE CONFIRM replaces all data by it, RESTORE CANCEL leaves the data as it is."
                )
        except (ApiError, ValueError, OSError) as e:
            print(f"the backup is not put back: {getattr(e, 'message', None) or e}")

    # --- radio stations ----------------------------------------------------------------------------------------

    def cmd_list_radios(self, args: str) -> None:
        """list_radios - list of all radio stations"""
        output = "list of all radio stations:\n"
        for radio in self._control.list_radios():
            tags = ", ".join(t["uid"] for t in radio["tags"]) or "-"
            output += f"[{radio['id']}] '{radio['name']}' url: {radio['url']} tags: {tags}\n"
        tinyjukebox.core.ui.tools.more(output)

    def cmd_add_radio(self, args: str) -> None:
        """add_radio - add a radio station (url of the stream or of an m3u/pls playlist): ADD_RADIO URL NAME"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected at least 2)")
            return
        try:
            radio = self._control.add_radio(" ".join(words[1:]), words[0])
        except ApiError as e:
            print(f"adding radio station failed: {e.message}")
            return
        print(f"added radio station [{radio['id']}] '{radio['name']}'.")

    def _update_radio(self, args: str, field: str) -> None:
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected at least 2)")
            return
        radio_id, value = words[0], " ".join(words[1:])
        try:
            radio = self._control.update_radio(radio_id, **{field: value})
        except ApiError as e:
            print(f"changing radio station failed: {e.message}")
            return
        print(f"[{radio['id']}] updated {field} to '{radio[field]}'")

    def cmd_set_radio_name(self, args: str) -> None:
        """set_radio_name - sets the name of a radio station: SET_RADIO_NAME RADIO-ID this is the new name"""
        self._update_radio(args, "name")

    def cmd_set_radio_url(self, args: str) -> None:
        """set_radio_url - sets the url of a radio station (a playing station restarts): SET_RADIO_URL RADIO-ID URL"""
        self._update_radio(args, "url")

    def cmd_set_radio_cover(self, args: str) -> None:
        """set_radio_cover - makes an image file the cover of a radio station: SET_RADIO_COVER RADIO-ID FILE"""
        given = self._id_and_image(args)
        if given is None:
            return
        try:
            radio = self._control.set_radio_cover(*given)
        except ApiError as e:
            print(f"changing radio station failed: {e.message}")
            return
        print(f"[{radio['id']}] '{radio['name']}' has a new cover.")

    def cmd_remove_radio(self, args: str) -> None:
        """remove_radio - remove a radio station that is not linked to a tag: REMOVE_RADIO RADIO-ID"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        try:
            radio = self._control.get_radio(words[0])
        except ApiError as e:
            print(e.message)
            return
        print(f"removing radio station [{radio['id']}] '{radio['name']}'.")
        if not _confirm():
            print("canceled.")
            return
        try:
            self._control.delete_radio(radio["id"])
        except ApiError as e:
            tags = ", ".join(t["uid"] for t in e.details.get("tags", []))
            print(f"removing radio station failed: {e.message}" + (f" ({tags})" if tags else ""))
            return
        print(f"removed radio station [{radio['id']}].")

    def cmd_test_radio(self, args: str) -> None:
        """test_radio - play a radio station or url for a minute - only while no tag is on the box.

        TEST_RADIO RADIO-ID - test a saved radio station
        TEST_RADIO URL - test a stream or m3u/pls playlist (e.g. before adding it)
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        url, name = words[0], None
        if "://" not in url:
            try:
                radio = self._control.get_radio(url)
            except ApiError as e:
                print(e.message)
                return
            url, name = radio["url"], radio["name"]
        try:
            self._control.test_radio(url, name)
        except ApiError as e:
            print(e.message)
            return
        print(f"testing '{name or url}' for a minute - album_info shows the state, stop_radio_test stops it.")

    def cmd_stop_radio_test(self, args: str) -> None:
        """stop_radio_test - stop the radio test"""
        try:
            self._control.stop_radio_test()
        except ApiError as e:
            print(e.message)
            return
        print("radio test stopped.")

    def _link_tag_radio(self, tag_uid: str, radio_id: str) -> None:
        try:
            tag = self._control.get_tag(tag_uid)
            radio = self._control.get_radio(radio_id)
        except ApiError as e:
            print(e.message)
            return
        print(f"linking tag '{tag['uid']}' ('{tag['name']}') with radio station '{radio['name']}'.")
        if not _confirm():
            print("canceled.")
            return
        result = self._control.link_tag_radio(tag_uid, radio_id)
        print(f"link result: tag {result['uid']} -> radio station {result['radio_id']}")
        if result["present"]:
            print("the tag is on the box - the radio station starts now.")

    def cmd_link_tag_radio(self, args: str) -> None:
        """link_tag_radio - links a tag with a radio station.

        LINK_TAG_RADIO TAG-UID RADIO-ID - link a specific tag with a radio station
        LINK_TAG_RADIO AUTO RADIO-ID - link the unlinked tag on the box with a radio station
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 2:
            print(f"wrong number of arguments (got {len(words)}, expected 2)")
            return
        tag_uid = self._tag_uid_or_auto(words[0])
        if tag_uid is not None:
            self._link_tag_radio(tag_uid, words[1])

    # --- entries of Spotify -------------------------------------------------------------------------------------

    def cmd_list_spotify(self, args: str) -> None:
        """list_spotify - list of all entries of Spotify"""
        output = "list of all entries of Spotify:\n"
        for entry in self._control.list_spotify():
            tags = ", ".join(t["uid"] for t in entry["tags"]) or "-"
            output += f"[{entry['id']}] '{entry['name']}' {entry['kind']} {entry['link']} tags: {tags}\n"
        tinyjukebox.core.ui.tools.more(output)

    def cmd_add_spotify(self, args: str) -> None:
        """add_spotify - add an entry of Spotify from its link (name and cover come from Spotify unless a name is
        given): ADD_SPOTIFY LINK [NAME]"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if not words:
            print("wrong number of arguments (got 0, expected at least 1)")
            return
        try:
            entry = self._control.add_spotify(words[0], " ".join(words[1:]) or None)
        except ApiError as e:
            print(f"adding entry of Spotify failed: {e.message}")
            return
        print(f"added entry of Spotify [{entry['id']}] '{entry['name']}' ({entry['kind']}).")

    def _update_spotify(self, args: str, field: str) -> None:
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) < 2:
            print(f"wrong number of arguments (got {len(words)}, expected 2)")
            return
        spotify_id, value = words[0], " ".join(words[1:])
        try:
            entry = self._control.update_spotify(spotify_id, **{field: value})
        except ApiError as e:
            print(f"changing entry of Spotify failed: {e.message}")
            return
        print(f"[{entry['id']}] updated {field} to '{entry[field]}'")

    def cmd_set_spotify_name(self, args: str) -> None:
        """set_spotify_name - sets the name of an entry of Spotify: SET_SPOTIFY_NAME SPOTIFY-ID this is the new name"""
        self._update_spotify(args, "name")

    def cmd_set_spotify_link(self, args: str) -> None:
        """set_spotify_link - sets the link of an entry of Spotify (a playing entry restarts): SET_SPOTIFY_LINK
        SPOTIFY-ID LINK"""
        self._update_spotify(args, "link")

    def cmd_set_spotify_cover(self, args: str) -> None:
        """set_spotify_cover - makes an image file the cover of an entry of Spotify: SET_SPOTIFY_COVER SPOTIFY-ID
        FILE"""
        given = self._id_and_image(args)
        if given is None:
            return
        try:
            entry = self._control.set_spotify_cover(*given)
        except ApiError as e:
            print(f"changing entry of Spotify failed: {e.message}")
            return
        print(f"[{entry['id']}] '{entry['name']}' has a new cover.")

    def cmd_remove_spotify(self, args: str) -> None:
        """remove_spotify - remove an entry of Spotify that is not linked to a tag: REMOVE_SPOTIFY SPOTIFY-ID"""
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 1:
            print(f"wrong number of arguments (got {len(words)}, expected 1)")
            return
        try:
            entry = self._control.get_spotify(words[0])
        except ApiError as e:
            print(e.message)
            return
        print(f"removing entry of Spotify [{entry['id']}] '{entry['name']}'.")
        if not _confirm():
            print("canceled.")
            return
        try:
            self._control.delete_spotify(entry["id"])
        except ApiError as e:
            tags = ", ".join(t["uid"] for t in e.details.get("tags", []))
            print(f"removing entry of Spotify failed: {e.message}" + (f" ({tags})" if tags else ""))
            return
        print(f"removed entry of Spotify [{entry['id']}].")

    def _link_tag_spotify(self, tag_uid: str, spotify_id: str) -> None:
        try:
            tag = self._control.get_tag(tag_uid)
            entry = self._control.get_spotify(spotify_id)
        except ApiError as e:
            print(e.message)
            return
        print(f"linking tag '{tag['uid']}' ('{tag['name']}') with entry of Spotify '{entry['name']}'.")
        if not _confirm():
            print("canceled.")
            return
        result = self._control.link_tag_spotify(tag_uid, spotify_id)
        print(f"link result: tag {result['uid']} -> entry of Spotify {result['spotify_id']}")
        if result["present"]:
            print("the tag is on the box - the entry starts now.")

    def cmd_link_tag_spotify(self, args: str) -> None:
        """link_tag_spotify - links a tag with an entry of Spotify.

        LINK_TAG_SPOTIFY TAG-UID SPOTIFY-ID - link a specific tag with an entry of Spotify
        LINK_TAG_SPOTIFY AUTO SPOTIFY-ID - link the unlinked tag on the box with an entry of Spotify
        """
        words = tinyjukebox.core.ui.tools.parse(args)
        if len(words) != 2:
            print(f"wrong number of arguments (got {len(words)}, expected 2)")
            return
        tag_uid = self._tag_uid_or_auto(words[0])
        if tag_uid is not None:
            self._link_tag_spotify(tag_uid, words[1])

    def cmd_link_current_tag_spotify(self, args: str) -> None:
        """link_current_tag_spotify - links the unlinked tag on the box with an entry of Spotify.

        LINK_CURRENT_TAG_SPOTIFY - show the entries of Spotify
        LINK_CURRENT_TAG_SPOTIFY SPOTIFY-ID - link the unlinked tag on the box with the entry
        """
        current_uid = self._control.current_unlinked_tag()
        if current_uid is None:
            print("No unlinked tag currently present.")
            return
        print(f"Using currently present unlinked tag: {current_uid}")
        words = tinyjukebox.core.ui.tools.parse(args)
        if not words:
            self.cmd_list_spotify("")
        elif len(words) == 1:
            self._link_tag_spotify(current_uid, words[0])
        else:
            print(f"wrong number of arguments (got {len(words)}, expected 0 or 1)")

    def cmd_link_current_tag_radio(self, args: str) -> None:
        """link_current_tag_radio - links the unlinked tag on the box with a radio station.

        LINK_CURRENT_TAG_RADIO - show the radio stations
        LINK_CURRENT_TAG_RADIO RADIO-ID - link the unlinked tag on the box with the radio station
        """
        current_uid = self._control.current_unlinked_tag()
        if current_uid is None:
            print("No unlinked tag currently present.")
            return
        print(f"Using currently present unlinked tag: {current_uid}")
        words = tinyjukebox.core.ui.tools.parse(args)
        if not words:
            self.cmd_list_radios("")
        elif len(words) == 1:
            self._link_tag_radio(current_uid, words[0])
        else:
            print(f"wrong number of arguments (got {len(words)}, expected 0 or 1)")
