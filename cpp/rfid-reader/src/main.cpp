// rfid-reader: watches the RC522 RFID reader and reports every change as one line on stdout.
//
//   ready                      the reader is initialized, no tag has been seen yet
//   tag 04:1A:2B:3C:4D:5E:6F   a tag has been placed (its complete uid, 4 or 7 bytes)
//   removed                    the tag has been removed
//
// The driver 'rfid' of tinyjukebox starts this program and reads the lines (src/tinyjukebox/devices/drivers/rfid.py).
//
// The read loop is the one of rfid-2-mqtt, which has been running on the box since 2021: PICC_IsCardPresent
// (WUPA - also answered by a halted tag) tells whether the tag is still there, PICC_HaltA puts it to sleep
// again, and a tag counts as removed only if a second check 100 ms later fails as well. Do not "simplify" it -
// the presence detection of the Python driver (libraries pi-rc522, mfrc522) did not work on the real reader.
//
// Around that loop the reader is kept alive: its reset line is driven (initReader), and a reader that has been
// reset nevertheless is initialized again (restoreReader) - a message on stderr tells so.
//
// Needs the fork https://github.com/tgd1975/RPi-MFRC522 (PICC_IsCardPresent) and root (bcm2835: /dev/mem).

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <getopt.h>
#include <poll.h>
#include <unistd.h>

#include "MFRC522.h"  // last: its Arduino.h defines macros (round, abs, ...) that break the standard headers

const auto POLL_INTERVAL = std::chrono::milliseconds(200);
const auto DEBOUNCE = std::chrono::milliseconds(100);

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
// looked for and before a tag counts as removed, so a tag that stays on the reader is not reported as removed.
void restoreReader(MFRC522 &rfid, int pin_rst, bool &failing) {
    if (isInitialized(rfid)) {
        failing = false;
        return;
    }
    initReader(rfid, pin_rst);
    if (isInitialized(rfid)) {
        std::cerr << "rfid-reader: the reader had been reset - initialized again" << std::endl;
        failing = false;
    } else if (!failing) {  // only once, this is tried again with every poll
        std::cerr << "rfid-reader: the reader has been reset and cannot be initialized - trying again" << std::endl;
        failing = true;
    }
}

void printUsage(const char *program) {
    std::cerr << "usage: " << program << " --pin-rst GPIO --pin-cs GPIO   (BCM numbers, e.g. 26 and 8)" << std::endl;
}

bool parsePin(const char *text, int &pin) {
    char *end = nullptr;
    long value = strtol(text, &end, 10);
    if (end == text || *end != '\0' || value < 0 || value > 53) {
        return false;
    }
    pin = static_cast<int>(value);
    return true;
}

int main(int argc, char **argv) {
    int pin_rst = -1;
    int pin_cs = -1;
    const struct option options[] = {
        {"pin-rst", required_argument, nullptr, 'r'},
        {"pin-cs", required_argument, nullptr, 'c'},
        {nullptr, 0, nullptr, 0},
    };
    int option;
    while ((option = getopt_long(argc, argv, "", options, nullptr)) != -1) {
        bool valid = false;
        if (option == 'r') {
            valid = parsePin(optarg, pin_rst);
        } else if (option == 'c') {
            valid = parsePin(optarg, pin_cs);
        }
        if (!valid) {
            printUsage(argv[0]);
            return 2;
        }
    }
    if (pin_rst < 0 || pin_cs < 0 || optind != argc) {
        printUsage(argv[0]);
        return 2;
    }

#ifndef RFID_READER_STUB  // the tests build with a stub of MFRC522.h - no hardware, no root
    if (geteuid()) {
        std::cerr << "rfid-reader must be executed as root (sudo)." << std::endl;
        return 1;
    }
#endif

    try {
        MFRC522 rfid = MFRC522(pin_cs, pin_rst);
        initReader(rfid, pin_rst);
        report("ready");

        bool card_present = false;
        bool failing = false;
        while (!readerIsGone()) {
            restoreReader(rfid, pin_rst, failing);
            if (rfid.PICC_IsCardPresent()) {
                if (!card_present) {
                    if (rfid.PICC_ReadCardSerial()) {
                        card_present = true;
                        report("tag " + uidToString(rfid.uid.uidByte, rfid.uid.size));
                    }
                }
                rfid.PICC_HaltA();
                rfid.PCD_StopCrypto1();
            } else {
                if (card_present) {
                    // debouncing - wait for 100 ms and try again
                    restoreReader(rfid, pin_rst, failing);  // a reset during the check above is no removed tag
                    std::this_thread::sleep_for(DEBOUNCE);
                    if (!rfid.PICC_IsCardPresent()) {
                        card_present = false;
                        report("removed");
                    } else {
                        rfid.PICC_HaltA();
                        rfid.PCD_StopCrypto1();
                    }
                }
            }
            std::this_thread::sleep_for(POLL_INTERVAL);
        }
    } catch (const std::runtime_error &e) {
        std::cerr << "rfid-reader: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
