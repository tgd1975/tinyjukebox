// Stub of MFRC522.h (RPi-MFRC522) for the tests of rfid-reader: no hardware, no bcm2835.
//
// The "tag on the reader" is the content of the file named by the environment variable RFID_STUB_TAG: bytes of the
// uid in hex, separated by blanks (e.g. "04 1A 2B 3C 4D 5E 6F"); empty or missing file: no tag.
// RFID_STUB_FAIL: the constructor throws like the library does if bcm2835_init fails.
// RFID_STUB_RESET: while the file named by it exists the reader "has been reset" - antenna off, it sees no tag.
// PCD_Init removes the file; with RFID_STUB_DEAD set it does not (a reader that cannot be initialized).
// RFID_STUB_PINS: the calls of digitalWrite and pinMode are appended to the file named by it, one per line.

#ifndef MFRC522_STUB_H
#define MFRC522_STUB_H

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

typedef uint8_t byte;

const byte HIGH = 1;
const byte OUTPUT = 1;

inline void logPin(const std::string &call, int pin, int value) {
    const char *path = getenv("RFID_STUB_PINS");
    if (path != nullptr) {
        std::ofstream(path, std::ios::app) << call << " " << pin << " " << value << std::endl;
    }
}

inline void digitalWrite(int pin, int value) { logPin("digitalWrite", pin, value); }
inline void pinMode(int pin, int mode) { logPin("pinMode", pin, mode); }

class MFRC522 {
public:
    typedef struct {
        byte size;
        byte uidByte[10];
        byte sak;
    } Uid;

    enum PCD_Register : byte { TxControlReg = 0x14 << 1 };

    Uid uid;

    MFRC522(byte, byte) {
        if (getenv("RFID_STUB_FAIL")) {
            throw std::runtime_error("bcm2835_init failed. Are you running as root?\n");
        }
    }

    void PCD_Init() {
        const char *path = getenv("RFID_STUB_RESET");
        if (path != nullptr && getenv("RFID_STUB_DEAD") == nullptr) {
            remove(path);
        }
    }
    void PICC_HaltA() {}
    void PCD_StopCrypto1() {}

    // TxControlReg: 0x83 with the antenna on (after PCD_Init), 0x80 after a reset
    byte PCD_ReadRegister(PCD_Register) { return isReset() ? 0x80 : 0x83; }

    bool PICC_IsCardPresent() { return !isReset() && !readTag().empty(); }

    bool PICC_ReadCardSerial() {
        std::vector<byte> tag = readTag();
        if (tag.empty() || tag.size() > sizeof(uid.uidByte)) {
            return false;
        }
        uid.size = static_cast<byte>(tag.size());
        for (size_t i = 0; i < tag.size(); i++) {
            uid.uidByte[i] = tag[i];
        }
        return true;
    }

private:
    static bool isReset() {
        const char *path = getenv("RFID_STUB_RESET");
        return path != nullptr && std::ifstream(path).good();
    }

    static std::vector<byte> readTag() {
        std::vector<byte> tag;
        const char *path = getenv("RFID_STUB_TAG");
        if (path == nullptr) {
            return tag;
        }
        std::ifstream file(path);
        std::string part;
        while (file >> part) {
            tag.push_back(static_cast<byte>(std::stoul(part, nullptr, 16)));
        }
        return tag;
    }
};

#endif  // MFRC522_STUB_H
