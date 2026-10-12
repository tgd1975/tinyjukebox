# rfid-reader (C++)

`rfid-reader` is the program that reads the RC522 RFID reader of the box. It is the only part of Tiny Jukebox that is
not Python. Source: [`cpp/rfid-reader/`](../../cpp/rfid-reader/).

| | |
| --- | --- |
| Source | `src/main.cpp` (one file, C++17) |
| Build | `Makefile`, with `g++`: for a release in the build of the slot (`scripts/build_slot.sh`), by hand on the box |
| Libraries | [bcm2835](http://www.airspayce.com/mikem/bcm2835/) 1.77 (installed, linked statically); fork [RPi-MFRC522](https://github.com/tgd1975/RPi-MFRC522) at tag `1.4.5-tgd.1` (sources, compiled into the program) |
| Installed as | `bin/rfid-reader` of every slot; `/usr/local/bin/rfid-reader` is a link to the one of the active slot (without slots: the file itself) |
| Started by | the Python driver `rfid` ([drivers.md](drivers.md), section 4), as root via `sudo -n` |
| Tests | `tests/devices/integration/test_rfid_reader.py`, built against `tests/stub/MFRC522.h` |

## 1. Interface

```text
rfid-reader --pin-rst GPIO --pin-cs GPIO        # BCM numbers; the box: --pin-rst 26 --pin-cs 8
```

**stdout** – one line per change, flushed at once (`std::endl`; stdout is a pipe, the driver must see every line
immediately):

| Line | Meaning |
| --- | --- |
| `ready` | the reader is initialized, no tag has been seen yet |
| `tag 04:1A:2B:3C:4D:5E:6F` | a tag was placed: its complete UID (4 or 7 bytes), hex, upper case, separated by colons |
| `removed` | the tag was removed |

**stderr** – messages, each prefixed with `rfid-reader:`. The driver writes them to the log as
`RFID - rfid-reader: …` (WARNING).

| Message | Meaning |
| --- | --- |
| `the reader had been reset - initialized again` | the RC522 had lost its setup and was set up again (section 3) |
| `the reader has been reset and cannot be initialized - trying again` | written once; the program keeps trying with every poll |
| `must be executed as root (sudo).` | started without root |

**Exit codes:**

| Code | Reason |
| --- | --- |
| 0 | nobody reads stdout any more (tinyjukebox has ended) |
| 1 | not root, or the library failed (e.g. `bcm2835_init`) |
| 2 | wrong arguments (usage on stderr); pins must be 0–53 |

## 2. Read loop

```text
init reader, drive RST high, report "ready"
loop until nobody reads stdout:
    restore the reader if it has been reset
    if PICC_IsCardPresent:                      # WUPA
        if no tag was present: PICC_ReadCardSerial -> report "tag <uid>"
        PICC_HaltA, PCD_StopCrypto1             # put the tag to sleep
    else if a tag was present:
        restore the reader if it has been reset
        wait 100 ms                             # DEBOUNCE
        if still not PICC_IsCardPresent: report "removed"
        else: PICC_HaltA, PCD_StopCrypto1
    wait 200 ms                                 # POLL_INTERVAL
```

A new tag is detected within about 0.2 s, a removal within about 0.3 s. A tag that replaces another one within one
poll interval is not seen as a change; in practice a tag is lifted and another one placed, which takes longer.

## 3. Design decisions

The box plays as long as the tag stands on it. This requires reliable presence detection: a tag that is still
there must never be reported as removed, and a removed tag must be noticed within a fraction of a second.

| Decision | Reason |
| --- | --- |
| The read loop is C++, not Python | Presence detection with the Python libraries `pi-rc522` and `mfrc522` did not work on the real reader. This loop has run on the box since 2021 (then as `rfid-2-mqtt`, reporting over MQTT). **It is not to be "simplified".** |
| `PICC_IsCardPresent` uses WUPA instead of REQA | A tag that `PICC_HaltA` has put to sleep answers WUPA but not REQA. The fork RPi-MFRC522 adds this function to the library. |
| Polling instead of the IRQ pin | The IRQ of the RC522 does not signal the removal of a tag. The IRQ pin is not connected to any function. |
| Removal needs a second failed check 100 ms later | A single missed answer must not stop playback. |
| The reset line (RST, GPIO 26) is driven high (`initReader`) | `PCD_Init` of the library leaves the pin as an input if it reads high. Then nothing drives the line, and a short dip resets the reader. The level is written before the pin becomes an output: an output that has never been set is low, and switching first would reset the reader that has just been initialized. |
| Before every poll, and before a tag counts as removed, the program checks whether the antenna is still on (`TxControlReg`, bits TX1/TX2) and initializes the reader again if not (`restoreReader`) | A reader that was reset (reset line, supply voltage) has lost its whole setup and sees no tag any more, without any error. Without the check the box stays deaf until the service is restarted. Checking before "removed" keeps a tag that stays on the reader from being reported as removed. |
| The program ends when nobody reads stdout (`poll` on stdout before every pass) | A reader left over from a killed tinyjukebox would disturb the next one: both would talk to the same RC522. |
| The program runs as root | bcm2835 accesses the SPI registers through `/dev/mem`. Only this program is root; the service runs as `pi` and calls it through a sudoers rule limited to this one path. |
| The complete UID in hex is the tag's identity | This is the format in `tags.json`. It must not change, or all tags lose their albums. |
| `MFRC522.h` is included last | Its `Arduino.h` defines macros (`round`, `abs`) that break the standard library headers. |

If the message `the reader had been reset` appears repeatedly in the log, check the wiring and the supply of the
reader ([operations.md](operations.md), section 9).

## 4. Build and install

**For a release** `scripts/build_slot.sh` builds the program for aarch64 and puts it into the slot as
`bin/rfid-reader`; a box gets it with the version, and nothing is compiled on the box
([library.md](library.md), section 6.15). The build keeps the program in its cache as long as `main.cpp`, the
`Makefile` and the versions of the two libraries are unchanged. The program contains both libraries – bcm2835 is
under the GPL-3.0, RPi-MFRC522 in the public domain –, so the slot gets their licence texts next to it, from
`cpp/rfid-reader/licenses/`.

**On the box** the playbook builds it in the repository (role `rfid`, [deployment.md](deployment.md), section 3) –
to try a change where the reader is:

```bash
cd /srv/tinyjukebox/tinyjukebox/cpp/rfid-reader
make MFRC522_DIR=/srv/tinyjukebox/cpp/RPi-MFRC522    # compiles main.cpp, MFRC522.cpp and the fork's Arduino layer
sudo systemctl stop tinyjukebox
sudo ./rfid-reader --pin-rst 26 --pin-cs 8           # try it by hand
sudo systemctl start tinyjukebox
```

On a box without slots `sudo make install` puts it to `/usr/local/bin/rfid-reader` (`PREFIX=/usr/local`), and
`sudo systemctl restart tinyjukebox` makes the driver start the new program. On a box with slots that path is a link
into the active slot: do not install over it; a changed reader comes with a release.

- `make` builds only if a source is newer than the program.
- `MFRC522Extended.cpp` of the fork is not compiled; it is not needed.
- The fork's tag `1.4.5-tgd.1` contains two corrections without which GCC 14 (Debian trixie) aborts:
  `#include <cstdint>` in `Serial.h` and a pointer comparison in `MFRC522Extended.cpp`.
- bcm2835 is downloaded from airspayce.com; its SHA-256 is pinned in `deploy/ansible/group_vars/all/main.yaml`
  (`rfid_bcm2835_sha256`).
- `dtparam=spi=on` must be set in `config.txt`.

Run it by hand (the service must not run – two readers disturb each other):

```bash
sudo systemctl stop tinyjukebox
sudo -n /usr/local/bin/rfid-reader --pin-rst 26 --pin-cs 8      # ready; place a tag: tag …; remove it: removed
```

## 5. Tests

| Test | What it covers |
| --- | --- |
| `tests/devices/unit/test_rfid.py` | the Python driver against `FakeRfidReader`, a process that the test controls through a FIFO |
| `tests/devices/integration/test_rfid_reader.py` | the real `main.cpp`, built with `g++ -DRFID_READER_STUB` against the stub header, running against the driver |

The integration test is skipped without `g++`. It checks the line format with the UIDs of the box, immediate
writing, a tag that is already placed at start, the reset line being driven high (level before direction), the
re-initialization after a reset, a reader that cannot be initialized (logged once), the end when the output is
closed, and the exit codes.

`tests/stub/MFRC522.h` replaces the library; with `RFID_READER_STUB` the root check is skipped. Environment
variables control the stub:

| Variable | Effect |
| --- | --- |
| `RFID_STUB_TAG` | file whose content is the "tag on the reader": UID bytes in hex separated by blanks; empty or missing: no tag |
| `RFID_STUB_FAIL` | the constructor throws, as the library does if `bcm2835_init` fails |
| `RFID_STUB_RESET` | while the file named by it exists, the reader "has been reset" (antenna off, sees no tag); `PCD_Init` removes the file |
| `RFID_STUB_DEAD` | `PCD_Init` does not remove that file: a reader that cannot be initialized |
| `RFID_STUB_PINS` | file to which the calls of `digitalWrite` and `pinMode` are appended |

No automated test covers the hardware itself (bcm2835, SPI, RC522). That is what the acceptance checklist is for
([deployment.md](deployment.md), section 8).
