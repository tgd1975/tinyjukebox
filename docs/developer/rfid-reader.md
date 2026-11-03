# rfid-reader (C++)

`rfid-reader` is the program that reads the RC522 RFID reader of the box. It is the only part of Tiny Jukebox that is
not Python. Source: [`cpp/rfid-reader/`](../../cpp/rfid-reader/).

| | |
| --- | --- |
| Source | `src/main.cpp` (one file, C++17) |
| Build | `Makefile`, with `g++`: for a release in the build of the slot (`scripts/build_slot.sh`), by hand on the box |
| Libraries | [bcm2835](http://www.airspayce.com/mikem/bcm2835/) 1.77 (installed, linked statically); fork [RPi-MFRC522](https://github.com/tgd1975/RPi-MFRC522) at tag `1.4.5-tgd.1` (sources, compiled into the program) |
| Installed as | `bin/rfid-reader` of every slot; `/usr/local/bin/rfid-reader` is a link to the launcher of root, which starts the one of the active slot only if the box trusts it ([box.md](box.md), section 7); without slots: the file itself |
| Started by | the Python driver `rfid` ([drivers.md](drivers.md), section 4), as root via `sudo -n` |
| Tests | `tests/devices/integration/test_rfid_reader.py`, built against `tests/stub/MFRC522.h` |

## 1. Interface

```text
rfid-reader --pin-rst GPIO --pin-cs GPIO [--alive SECONDS]   # BCM numbers; the box: --pin-rst 26 --pin-cs 8
```

| Option | Meaning |
| --- | --- |
| `--pin-rst` | the reset line (NRSTPD) of the RC522 |
| `--pin-cs` | chip select. The library drives it itself as a GPIO and switches the chip select of the SPI hardware off, so any free pin will do; the box uses 8 (CE0) |
| `--alive` | seconds between two `alive` lines: more than 0, at most 3600; default 10, which the driver expects. Only the tests give it |

The pins are 0–53, two different ones and none of 9, 10, 11 (MISO, MOSI, SCLK of SPI0): the library would make them
outputs, and SPI would not work any more until the next boot.

**stdout** – one line per change, flushed at once (`std::endl`; stdout is a pipe, the driver must see every line
immediately):

| Line | Meaning |
| --- | --- |
| `ready` | the reader is initialized, no tag has been seen yet |
| `tag 04:1A:2B:3C:4D:5E:6F` | a tag was placed: its complete UID (4 or 7 bytes), hex, upper case, separated by colons. Also a tag that has taken the place of the last one (a quick swap; of two stacked tags the one that was reported lifted) – without `removed` in between |
| `removed` | the tag was removed |
| `alive` | the program still runs: at its start, before it touches the reader, and then every 10 s (`--alive`) |

**stderr** – messages, each prefixed with `rfid-reader:`. The driver writes them to the log as
`RFID - rfid-reader: …` (WARNING).

| Message | Meaning |
| --- | --- |
| `the reader had been reset - initialized again` | the RC522 had lost its setup and was set up again (section 3) |
| `the reader has been reset and cannot be initialized - trying again` | written once; the program keeps trying with every poll |
| `must be executed as root (sudo).` | started without root |
| `bcm2835_init failed. Are you running as root?` | the library failed (exit code 1). The library prints the message to stdout as well before it throws; the program drops that copy, so the driver logs it once |

**Exit codes:**

| Code | Reason |
| --- | --- |
| 0 | nobody reads stdout any more (tinyjukebox has ended) |
| 1 | not root, or the library failed (e.g. `bcm2835_init`) – every `std::exception` ends the program this way, with its message |
| 2 | wrong arguments (usage on stderr): pins as above, `--alive` as above |

## 2. Read loop

```text
report "alive", init reader, drive RST high, report "ready"
loop until nobody reads stdout:
    report "alive" if the last one is 10 s ago (--alive)
    restore the reader if it has been reset
    look: PICC_IsCardPresent (WUPA); if a tag answers: PICC_ReadCardSerial (select: its uid),
          PICC_HaltA, PCD_StopCrypto1                    # put the tag to sleep
    if a tag answers and none was there: report "tag <uid>"          # (a uid that cannot be read: next poll)
    else if a tag was there, and none answers or another uid does:   # removed, or replaced?
        if none answered: PICC_HaltA                     # a tag that was not understood is READY (section 3)
        restore the reader if it has been reset
        wait 100 ms                                      # DEBOUNCE
        restore the reader if it has been reset
        look again:
            the same other uid - or, if none answered before, any other uid: report "tag <uid>"
            no tag, and the reader was not reset in this pass: report "removed"
            else: nothing changes                        # the tag is still there, or the next poll decides
    wait 200 ms                                          # POLL_INTERVAL
```

A new tag is detected within about 0.2 s, a removal within about 0.3 s, a tag that takes the place of another one
within about 0.3 s as well. Two stacked tags both answer WUPA (a collision, which counts as present); the select of the
library picks one of them, always the same. If that one is lifted, the other one is reported.

## 3. Design decisions

The box plays as long as the tag stands on it. This requires reliable presence detection: a tag that is still
there must never be reported as removed, and a removed tag must be noticed within a fraction of a second.

| Decision | Reason |
| --- | --- |
| The read loop is C++, not Python | Presence detection with the Python libraries `pi-rc522` and `mfrc522` did not work on the real reader. This loop has run on the box since 2021 (then as `rfid-2-mqtt`, reporting over MQTT). **It is not to be "simplified".** |
| `PICC_IsCardPresent` uses WUPA instead of REQA | A tag that `PICC_HaltA` has put to sleep answers WUPA but not REQA. The fork RPi-MFRC522 adds this function to the library. |
| Polling instead of the IRQ pin | The IRQ of the RC522 does not signal the removal of a tag. The IRQ pin is not connected to any function. |
| Removal needs a second failed check 100 ms later | A single missed answer must not stop playback. |
| The uid is read with every check, not only for a new tag | Of two stacked tags the select picks one; if that one was lifted and the other stayed, the loop saw "still present" and never reported the other one – the first album played on. A quick swap was not reported either. |
| Another uid needs a second look 100 ms later, like a removal | With two tags in the field answers get lost more often. If the picked tag misses one answer, the other one answers alone – a single odd answer must not switch the album. After no answer at all, any other uid in the second look counts: the old tag has then missed twice. A uid that cannot be read changes nothing. |
| After a check without an answer, `PICC_HaltA` before the second check | A garbled answer to WUPA can leave the tag READY. A READY tag does not answer the next WUPA but falls back to IDLE or HALT (ISO/IEC 14443-3) – the second check would fail as well: "removed", and "tag" with the next poll (the album would start again). HLTA makes a READY tag fall back too, and IDLE or HALT tags ignore it: the second check is independent of the first. |
| The reset line (RST, GPIO 26) is driven high (`initReader`) | `PCD_Init` of the library leaves the pin as an input if it reads high. Then nothing drives the line, and a short dip resets the reader. The level is written before the pin becomes an output: an output that has never been set is low, and switching first would reset the reader that has just been initialized. |
| Before every poll, and before and after the wait before a tag counts as removed, the program checks whether the antenna is still on (`TxControlReg`, bits TX1/TX2) and initializes the reader again if not (`restoreReader`) | A reader that was reset (reset line, supply voltage) has lost its whole setup and sees no tag any more, without any error. Without the check the box stays deaf until the service is restarted. A pass in which the reader had to be initialized again never reports "removed": a check just before the reset or just after it (the tags in the field have just got power again) says nothing about the tag – the next poll decides. A reader that cannot be initialized does report "removed". |
| `alive` at the start and every 10 s | A program that hangs (a stall in SPI, a stopped process) cannot say so itself. The driver ends a program that has written `alive` once and then nothing for three intervals, and starts it again. The first one comes before the reader is touched, so a hang in its initialization is noticed too. |
| The program ends when nobody reads stdout (`poll` on stdout before every pass) | A reader left over from a killed tinyjukebox would disturb the next one: both would talk to the same RC522. The driver relies on it as well: `sudo` passes SIGTERM on, but SIGKILL reaches only `sudo` – the driver then closes its end of the pipe and rfid-reader ends by itself. |
| The program runs as root | bcm2835 accesses the SPI registers through `/dev/mem`. Only this program is root; the service runs as `pi` and calls it through a sudoers rule limited to this one path. |
| SPI0 is set up by the kernel (`dtparam=spi=on`), not by the program | The library never calls `bcm2835_spi_begin()` (pins 7–11 to SPI0): its `SPI.h` declares the object `SPI`, whose constructor would do it, but nothing defines that object, and the transfers only set clock, mode and bit order. Without `dtparam=spi=on` the reader does not answer. Left as it is: it cannot be tested without the box. |
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

- `make` builds if `main.cpp`, a source or header of the fork or the `Makefile` is newer than the program. Without the
  fork in `MFRC522_DIR` it says so (`no RPi-MFRC522 found`).
- It compiles with `-Wall -Wextra` (`WARNINGS`); the build has no warnings, and the tests make every warning of
  `main.cpp` an error.
- `MFRC522Extended.cpp` of the fork is not compiled; it is not needed.
- The fork's tag `1.4.5-tgd.1` contains two corrections without which GCC 14 (Debian trixie) aborts:
  `#include <cstdint>` in `Serial.h` and a pointer comparison in `MFRC522Extended.cpp`.
- bcm2835 is downloaded from airspayce.com; its SHA-256 is pinned in `deploy/ansible/group_vars/all/main.yaml`
  (`rfid_bcm2835_sha256`).
- `dtparam=spi=on` must be set in `config.txt`: the program does not set up SPI0 itself (section 3).

Run it by hand (the service must not run – two readers disturb each other):

```bash
sudo systemctl stop tinyjukebox
sudo -n /usr/local/bin/rfid-reader --pin-rst 26 --pin-cs 8      # alive, ready; place a tag: tag …; remove it: removed
```

## 5. Tests

| Test | What it covers |
| --- | --- |
| `tests/devices/unit/test_rfid.py` | the Python driver against `FakeRfidReader`, a process that the test controls through a FIFO |
| `tests/devices/integration/test_rfid_reader.py` | the real `main.cpp`, built with `g++ -DRFID_READER_STUB` against the stub header, running against the driver |

The integration test is skipped without `g++`. It builds `main.cpp` with `CXXFLAGS` and `WARNINGS` of the `Makefile`
and `-Werror`. It checks the line format with the UIDs of the box, immediate writing, a tag that is already placed at
start, the reset line being driven high (level before direction), the debounce (one missed answer changes nothing, two
are a removal), the halt after a garbled answer, the re-initialization after a reset – also one during the wait before
the second check –, a reader that cannot be initialized (logged once), a quick swap, two stacked tags (the one that
stays is reported; another uid once changes nothing), a failing select, `alive` (and that the driver ends and starts
again a program that has stopped), the end when the output is closed, the error of the library logged once, the
arguments and the exit codes.

`tests/stub/MFRC522.h` replaces the library; with `RFID_READER_STUB` the root check is skipped. Like the library it
defines the macros of its `Arduino.h` and of `bcm2835.h` (`round`, `abs`, `digitalWrite`, `pinMode`, `HIGH`, …): a
standard header included after it breaks the build of the tests as it would break the one of the box. Its tags follow
the states of ISO/IEC 14443-3 (IDLE, READY, ACTIVE, HALT): WUPA wakes IDLE and HALT tags, a READY tag ignores WUPA and
HLTA and falls back, the select picks one of several tags as the anticollision of the library does (the tag with the bit
set at the first bit in which the UIDs differ). Environment variables control the stub:

| Variable | Effect |
| --- | --- |
| `RFID_STUB_TAG` | file whose content is the "tags on the reader": one per line, UID bytes in hex separated by blanks; empty or missing: no tag; several lines: stacked tags |
| `RFID_STUB_EVENTS` | file of words, each used up by the next operation of its kind (the file is removed with the last one): `miss` (the next WUPA reaches no tag), `miss@UID` (it does not reach that tag), `garble` (the tags hear it but their answer is not understood: they stay READY), `noselect` (the next select fails), `reset` (the reader is reset right after the next check of its antenna – while the program waits for the second check) |
| `RFID_STUB_FAIL` | the constructor prints the error to stdout and throws, as the library does if `bcm2835_init` fails |
| `RFID_STUB_RESET` | while the file named by it exists, the reader "has been reset" (antenna off, sees no tag); `PCD_Init` removes the file |
| `RFID_STUB_DEAD` | `PCD_Init` does not remove that file: a reader that cannot be initialized |
| `RFID_STUB_PINS` | file to which the calls of `digitalWrite` and `pinMode` are appended |
| `RFID_STUB_LOG` | file to which what the reader sends is appended: `WUPA answer` / `none` / `miss` / `garbled`, `SELECT <uid>` / `fail`, `HLTA`, `INIT` |

No automated test covers the hardware itself (bcm2835, SPI, RC522). That is what the acceptance checklist is for
([deployment.md](deployment.md), section 8).
