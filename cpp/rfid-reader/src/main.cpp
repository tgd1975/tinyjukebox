// rfid-reader: watches the RC522 RFID reader and reports every change as one line on stdout.
//
//   ready                      the reader is initialized, no tag has been seen yet
//   tag 04:1A:2B:3C:4D:5E:6F   a tag has been placed (its complete uid, 4 or 7 bytes) - also one that has taken the
//                              place of the last one without a "removed" in between
//   removed                    the tag has been removed
//   alive                      the program still runs: at its start and then every 10 s (--alive SECONDS)
//
// The driver 'rfid' of tinyjukebox starts this program and reads the lines (src/tinyjukebox/devices/drivers/rfid.py).
// It ends a program that has been silent for three intervals after its first "alive", and starts it again.
//
// The read loop is the one of rfid-2-mqtt, which has been running on the box since 2021: PICC_IsCardPresent
// (WUPA - also answered by a halted tag) tells whether the tag is still there, PICC_HaltA puts it to sleep
// again, and a tag counts as removed only if a second check 100 ms later fails as well. Do not "simplify" it -
// the presence detection of the Python driver (libraries pi-rc522, mfrc522) did not work on the real reader.
// On top of it: the uid is read with every check, so a tag that takes the place of another one is reported (after a
// second look, like a removal), and a tag that has not been understood is halted before the second check.
//
// Around that loop the reader is kept alive: its reset line is driven (initReader), and a reader that has been
// reset nevertheless is initialized again (restoreReader) - a message on stderr tells so.
//
// SPI: the library never sets up the SPI interface itself. Its SPI.h declares the object SPI, whose constructor would
// call bcm2835_spi_begin() (pins 7-11 to SPI0), but nothing defines that object; the transfers only set clock, mode
// and bit order. SPI works because the kernel has set up SPI0 at boot (dtparam=spi=on in config.txt). That is left as
// it is - the hardware cannot be tested without the box.
//
// Needs the fork https://github.com/tgd1975/RPi-MFRC522 (PICC_IsCardPresent) and root (bcm2835: /dev/mem).

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <getopt.h>
#include <poll.h>
#include <stdio_ext.h>
#include <unistd.h>

#include "MFRC522.h"  // last: its Arduino.h defines macros (round, abs, ...) that break the standard headers

const auto POLL_INTERVAL = std::chrono::milliseconds(200);
const auto DEBOUNCE = std::chrono::milliseconds(100);
const double ALIVE_INTERVAL = 10;  // seconds - the driver expects "alive" this often (rfid.py, ALIVE_INTERVAL)

std::string formatAs02X(uint8_t hex) {
    char buff[100];
    snprintf(buff, sizeof(buff), "%02X", hex);
    return buff;
}

std::string uidToString(uint8_t *buffer, size_t bufferSize) {
    std::string result = "";
    for (size_t i = 0; i < bufferSize; i++) {
        result.append(":" + formatAs02X(buffer[i]));
    }
    if (!result.empty()) {
        result = result.substr(1);
    }
    return result;
}

// std::endl flushes: stdout is a pipe, the driver must see every line at once
void report(const std::string &line) {
    std::cout << line << std::endl;
}

// true if nobody reads stdout any more (tinyjukebox has ended) - a reader that stays behind would disturb the next one
bool readerIsGone() {
    struct pollfd out = {STDOUT_FILENO, 0, 0};
    return poll(&out, 1, 0) > 0 && (out.revents & (POLLERR | POLLHUP | POLLNVAL));
}

// PCD_Init leaves the reset pin of the reader (NRSTPD, low: reset) as an input if it reads high. Then nothing drives
// the line, and a short dip resets the reader - so drive it high. The level first: an output that has never been
// set is low, switching first would reset the reader that has just been initialized.
void initReader(MFRC522 &rfid, int pin_rst) {
    rfid.PCD_Init();
    digitalWrite(pin_rst, HIGH);
    pinMode(pin_rst, OUTPUT);
}

// false after a reset of the reader: PCD_Init switches the antenna on (TX1 and TX2), a reset switches it off
bool isInitialized(MFRC522 &rfid) {
    return (rfid.PCD_ReadRegister(MFRC522::TxControlReg) & 0x03) == 0x03;
}

// A reader that has been reset (reset line, supply voltage) has lost its whole setup and sees no tag any more - on
// the box (2026-10-04) it stayed that way, unnoticed, until tinyjukebox was started again. Called before the tag is
// looked for and around the wait before a tag counts as removed. Returns true if the reader had been reset and has
// been initialized again: a check just before says nothing about the tag, nor does one just after (the field has
// just been switched on again) - such a check never makes a removed tag.
bool restoreReader(MFRC522 &rfid, int pin_rst, bool &failing) {
    if (isInitialized(rfid)) {
        failing = false;
        return false;
    }
    initReader(rfid, pin_rst);
    if (isInitialized(rfid)) {
        std::cerr << "rfid-reader: the reader had been reset - initialized again" << std::endl;
        failing = false;
        return true;
    }
    if (!failing) {  // only once, this is tried again with every poll
        std::cerr << "rfid-reader: the reader has been reset and cannot be initialized - trying again" << std::endl;
        failing = true;
    }
    return false;
}

// One look: true if a tag answers WUPA (two tags at once collide - that counts as present too). Its uid goes to uid if
// the select works (of stacked tags it picks one, always the same), else uid stays empty. Then the tag is put to sleep
// again: a halted tag answers WUPA (not REQA) - it is woken by the next look.
bool lookForTag(MFRC522 &rfid, std::string &uid) {
    uid.clear();
    if (!rfid.PICC_IsCardPresent()) {
        return false;
    }
    if (rfid.PICC_ReadCardSerial()) {
        uid = uidToString(rfid.uid.uidByte, rfid.uid.size);
    }
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    return true;
}

void printUsage(const char *program) {
    std::cerr << "usage: " << program << " --pin-rst GPIO --pin-cs GPIO [--alive SECONDS]" << std::endl
              << "  BCM numbers, e.g. 26 and 8: two different pins, none of 9, 10, 11 (MISO, MOSI, SCLK of SPI0)"
              << std::endl
              << "  --alive: \"alive\" every SECONDS (default 10)" << std::endl;
}

// 0-53, the GPIOs of the BCM2835 - but not 9, 10, 11: the library would make the data and clock lines of SPI0 outputs,
// and SPI would not work any more until they are set up again (boot)
bool parsePin(const char *text, int &pin) {
    char *end = nullptr;
    long value = strtol(text, &end, 10);
    if (end == text || *end != '\0' || value < 0 || value > 53 || (value >= 9 && value <= 11)) {
        return false;
    }
    pin = static_cast<int>(value);
    return true;
}

// more than 0, at most an hour
bool parseSeconds(const char *text, double &seconds) {
    char *end = nullptr;
    double value = strtod(text, &end);
    if (end == text || *end != '\0' || !(value > 0) || value > 3600) {
        return false;
    }
    seconds = value;
    return true;
}

std::string withoutNewline(const char *text) {
    std::string result = text;
    while (!result.empty() && (result.back() == '\n' || result.back() == '\r')) {
        result.pop_back();
    }
    return result;
}

int main(int argc, char **argv) {
    int pin_rst = -1;
    int pin_cs = -1;
    double alive_seconds = ALIVE_INTERVAL;
    const struct option options[] = {
        {"pin-rst", required_argument, nullptr, 'r'},
        {"pin-cs", required_argument, nullptr, 'c'},
        {"alive", required_argument, nullptr, 'a'},
        {nullptr, 0, nullptr, 0},
    };
    int option;
    while ((option = getopt_long(argc, argv, "", options, nullptr)) != -1) {
        bool valid = false;
        if (option == 'r') {
            valid = parsePin(optarg, pin_rst);
        } else if (option == 'c') {
            valid = parsePin(optarg, pin_cs);
        } else if (option == 'a') {
            valid = parseSeconds(optarg, alive_seconds);
        }
        if (!valid) {
            printUsage(argv[0]);
            return 2;
        }
    }
    if (pin_rst < 0 || pin_cs < 0 || pin_rst == pin_cs || optind != argc) {
        printUsage(argv[0]);
        return 2;
    }
    const std::chrono::duration<double> alive_interval(alive_seconds);

#ifndef RFID_READER_STUB  // the tests build with a stub of MFRC522.h - no hardware, no root
    if (geteuid()) {
        std::cerr << "rfid-reader must be executed as root (sudo)." << std::endl;
        return 1;
    }
#endif

    // the first one before the reader is touched: the driver notices a program that hangs in it as well
    report("alive");
    auto alive_at = std::chrono::steady_clock::now();
    try {
        MFRC522 rfid = MFRC522(pin_cs, pin_rst);
        initReader(rfid, pin_rst);
        report("ready");

        std::string tag;  // the uid of the tag on the reader - empty: none
        bool failing = false;
        while (!readerIsGone()) {
            if (std::chrono::steady_clock::now() - alive_at >= alive_interval) {
                report("alive");
                alive_at = std::chrono::steady_clock::now();
            }
            restoreReader(rfid, pin_rst, failing);
            std::string uid;
            bool present = lookForTag(rfid, uid);
            if (present && tag.empty()) {
                if (!uid.empty()) {  // a uid that cannot be read: the next look tries again
                    tag = uid;
                    report("tag " + tag);
                }
            } else if (!tag.empty() && (!present || (!uid.empty() && uid != tag))) {
                // the tag does not answer, or another one does (swapped quickly, or of two stacked tags the reported
                // one lifted): look again 100 ms later - a single missed or odd answer must not change the album
                if (!present) {
                    rfid.PICC_HaltA();  // a tag whose answer was garbled is READY: it would ignore the next WUPA
                }
                bool reset = restoreReader(rfid, pin_rst, failing);  // a reset during the check above is no removed tag
                std::this_thread::sleep_for(DEBOUNCE);
                if (restoreReader(rfid, pin_rst, failing)) {  // nor is a reset during the wait
                    reset = true;
                }
                std::string again;
                if (lookForTag(rfid, again)) {
                    // the other uid twice; or, after no answer at all, any other uid - the old tag has missed twice
                    if (!again.empty() && again != tag && (!present || again == uid)) {
                        tag = again;
                        report("tag " + tag);
                    }
                } else if (!present && !reset) {
                    tag.clear();
                    report("removed");
                }
            }
            std::this_thread::sleep_for(POLL_INTERVAL);
        }
    } catch (const std::exception &e) {
        // The library prints the error of bcm2835_init to stdout before it throws. stdout is a pipe and therefore
        // buffered: the line has not been written yet - drop it, the message goes to stderr (the driver reads both and
        // would log it twice). Lines of this program are never in the buffer: report() flushes.
        __fpurge(stdout);
        std::cerr << "rfid-reader: " << withoutNewline(e.what()) << std::endl;
        return 1;
    }
    return 0;
}
