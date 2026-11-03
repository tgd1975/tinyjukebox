// Stub of MFRC522.h (RPi-MFRC522) for the tests of rfid-reader: no hardware, no bcm2835.
//
// Like the library it brings the macros of its Arduino.h and bcm2835.h (round, abs, digitalWrite, pinMode, HIGH, LOW,
// ...), so it has to be included last here as well: a standard header included after it does not compile. The tests
// build main.cpp with the flags of the Makefile and -Werror.
//
// The reader is controlled through files named by environment variables:
//
// RFID_STUB_TAG     the tags on the reader, one per line: the bytes of the uid in hex, separated by blanks
//                   (e.g. "04 1A 2B 3C 4D 5E 6F"); empty or missing file: no tag. Several lines: stacked tags. They
//                   all answer WUPA (a collision), the select picks the one with the bit set at the first bit in
//                   which the uids differ (bytes in order, each from its lowest bit) - as the anticollision of the
//                   library does.
// RFID_STUB_EVENTS  words, each one used up by the next operation of its kind; the file is removed when all are used:
//                     miss       the next WUPA (with a tag on the reader) reaches no tag
//                     miss@UID   the next WUPA does not reach the tag UID (as printed: 04:1A:...), the others answer
//                     garble     the next WUPA reaches the tags, but their answer is not understood - they are
//                                READY now
//                     noselect   the next select fails
//                     reset      the reader is reset right after its antenna has been checked the next time, i.e.
//                                while the program waits before it looks again
// RFID_STUB_RESET   while the file named by it exists the reader "has been reset": antenna off (TxControlReg 0x80),
//                   no tag answers. PCD_Init removes the file; with RFID_STUB_DEAD set it does not (a reader that
//                   cannot be initialized).
// RFID_STUB_FAIL    the constructor prints the error to stdout and throws, as the library does if bcm2835_init fails.
// RFID_STUB_PINS    the calls of digitalWrite and pinMode are appended to the file named by it, one per line.
// RFID_STUB_LOG     what the reader sends is appended to the file named by it, one line each: "WUPA answer",
//                   "WUPA none", "WUPA miss", "WUPA garbled", "SELECT <uid>", "SELECT fail", "HLTA", "INIT".
//
// The tags follow the states of ISO/IEC 14443-3: a tag that comes into the field is IDLE; WUPA makes an IDLE or HALT
// tag READY, and it answers; the select makes the chosen READY tag ACTIVE, the other READY tags fall back; HLTA makes
// an ACTIVE tag HALT. Any other command makes a READY or ACTIVE tag fall back to IDLE (to HALT if it was woken from
// there) without an answer - for a READY tag WUPA and HLTA are such commands, for an ACTIVE one WUPA. A reset or
// PCD_Init switches the field off: the tags start again as IDLE.

#ifndef MFRC522_STUB_H
#define MFRC522_STUB_H

// what MFRC522.h of the library and its Arduino.h include - and for the stub itself <cstdio> (comes with <string>) and
// <stdexcept>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <cstdio>
#include <stdexcept>
#include <string>

// --- the macros of bcm2835.h that the library uses, and all of its Arduino.h -----------------------------------------

#define HIGH 0x1
#define LOW 0x0
#define delay(x) bcm2835_delay(x)
#define delayMicroseconds(x) bcm2835_delayMicroseconds(x)

typedef enum { BCM2835_GPIO_FSEL_INPT = 0x00, BCM2835_GPIO_FSEL_OUTP = 0x01 } bcm2835FunctionSelect;

#define INPUT BCM2835_GPIO_FSEL_INPT
#define OUTPUT BCM2835_GPIO_FSEL_OUTP

#define PI 3.1415926535897932384626433832795
#define HALF_PI 1.5707963267948966192313216916398
#define TWO_PI 6.283185307179586476925286766559
#define DEG_TO_RAD 0.017453292519943295769236907684886
#define RAD_TO_DEG 57.295779513082320876798154814105
#define EULER 2.718281828459045235360287471352

#define SERIAL 0x0
#define DISPLAY 0x1

#define LSBFIRST 0
#define MSBFIRST 1

#define CHANGE 1
#define FALLING 2
#define RISING 3

#ifdef abs
#undef abs
#endif

#define abs(x) ((x) > 0 ? (x) : -(x))
#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))
#define round(x) ((x) >= 0 ? (long)((x) + 0.5) : (long)((x)-0.5))
#define radians(deg) ((deg)*DEG_TO_RAD)
#define degrees(rad) ((rad)*RAD_TO_DEG)
#define sq(x) ((x) * (x))

#define lowByte(w) ((uint8_t)((w)&0xff))
#define highByte(w) ((uint8_t)((w) >> 8))

#define bitRead(value, bit) (((value) >> (bit)) & 0x01)
#define bitSet(value, bit) ((value) |= (1UL << (bit)))
#define bitClear(value, bit) ((value) &= ~(1UL << (bit)))
#define bitToggle(value, bit) ((value) ^= (1UL << (bit)))
#define bitWrite(value, bit, bitvalue) ((bitvalue) ? bitSet(value, bit) : bitClear(value, bit))
#define F(s) s
#define digitalWrite(pin, val) bcm2835_gpio_write(pin, val)
#define pinMode(pin, val) bcm2835_gpio_fsel(pin, val)
#define digitalRead(pin) bcm2835_gpio_lev(pin)

typedef unsigned int word;
typedef bool boolean;
typedef uint8_t byte;

// --- the files that control the stub ---------------------------------------------------------------------------------

namespace rfid_stub {

const int MAX_TAGS = 4;
const char *const BLANKS = " \t\r\n";

inline bool exists(const char *variable) {
    const char *name = getenv(variable);
    FILE *file = name != nullptr ? fopen(name, "r") : nullptr;
    if (file == nullptr) {
        return false;
    }
    fclose(file);
    return true;
}

inline std::string read(const char *variable) {
    std::string text;
    const char *name = getenv(variable);
    FILE *file = name != nullptr ? fopen(name, "r") : nullptr;
    if (file != nullptr) {
        char buffer[256];
        size_t count;
        while ((count = fread(buffer, 1, sizeof(buffer), file)) > 0) {
            text.append(buffer, count);
        }
        fclose(file);
    }
    return text;
}

inline void write(const char *variable, const std::string &text, const char *mode) {
    const char *name = getenv(variable);
    FILE *file = name != nullptr ? fopen(name, mode) : nullptr;
    if (file != nullptr) {
        fputs(text.c_str(), file);
        fclose(file);
    }
}

inline void append(const char *variable, const std::string &line) { write(variable, line + "\n", "a"); }

// the next word of RFID_STUB_EVENTS, empty if there is none
inline std::string nextEvent() {
    std::string text = read("RFID_STUB_EVENTS");
    size_t start = text.find_first_not_of(BLANKS);
    if (start == std::string::npos) {
        return "";
    }
    return text.substr(start, text.find_first_of(BLANKS, start) - start);
}

// uses up the next word of RFID_STUB_EVENTS - the file is removed with the last one
inline void useEvent() {
    std::string text = read("RFID_STUB_EVENTS");
    size_t end = text.find_first_of(BLANKS, text.find_first_not_of(BLANKS));
    std::string rest = end == std::string::npos ? "" : text.substr(end);
    if (rest.find_first_not_of(BLANKS) == std::string::npos) {
        remove(getenv("RFID_STUB_EVENTS"));
    } else {
        write("RFID_STUB_EVENTS", rest, "w");
    }
}

// true if the next word is `word` - it is used up then
inline bool event(const std::string &word) {
    if (nextEvent() != word) {
        return false;
    }
    useEvent();
    return true;
}

// "04 1A 2B" -> the three bytes
inline std::string parseUid(const std::string &line) {
    std::string uid;
    size_t start = 0;
    while ((start = line.find_first_not_of(BLANKS, start)) != std::string::npos) {
        size_t end = line.find_first_of(BLANKS, start);
        uid.push_back(static_cast<char>(strtoul(line.substr(start, end - start).c_str(), nullptr, 16)));
        start = end;
    }
    return uid;
}

// the bytes of a uid as rfid-reader prints them: 04:1A:2B
inline std::string hex(const std::string &uid) {
    std::string text;
    for (char value : uid) {
        char part[4];
        snprintf(part, sizeof(part), "%02X", static_cast<unsigned char>(value));
        text += (text.empty() ? "" : ":") + std::string(part);
    }
    return text;
}

}  // namespace rfid_stub

// --- bcm2835: the pins -----------------------------------------------------------------------------------------------

inline void bcm2835_gpio_write(uint8_t pin, uint8_t on) {
    rfid_stub::append("RFID_STUB_PINS", "digitalWrite " + std::to_string(pin) + " " + std::to_string(on));
}
inline void bcm2835_gpio_fsel(uint8_t pin, uint8_t mode) {
    rfid_stub::append("RFID_STUB_PINS", "pinMode " + std::to_string(pin) + " " + std::to_string(mode));
}
inline uint8_t bcm2835_gpio_lev(uint8_t) { return HIGH; }
inline void bcm2835_delay(unsigned int) {}
inline void bcm2835_delayMicroseconds(uint64_t) {}

// --- the reader ------------------------------------------------------------------------------------------------------

class MFRC522 {
public:
    enum PCD_Register : byte { TxControlReg = 0x14 << 1 };

    enum StatusCode : byte {
        STATUS_OK,
        STATUS_ERROR,
        STATUS_COLLISION,
        STATUS_TIMEOUT,
        STATUS_NO_ROOM,
        STATUS_INTERNAL_ERROR,
        STATUS_INVALID,
        STATUS_CRC_WRONG,
        STATUS_MIFARE_NACK = 0xff
    };

    typedef struct {
        byte size;
        byte uidByte[10];
        byte sak;
    } Uid;

    Uid uid;

    MFRC522(byte, byte) {
        if (getenv("RFID_STUB_FAIL")) {
            printf("bcm2835_init failed. Are you running as root?\n");  // the library prints it to stdout first
            throw std::runtime_error("bcm2835_init failed. Are you running as root?\n");
        }
    }

    void PCD_Init() {
        note("INIT");
        count = 0;  // the field is switched off and on again: the tags start anew
        const char *path = getenv("RFID_STUB_RESET");
        if (path != nullptr && getenv("RFID_STUB_DEAD") == nullptr) {
            remove(path);
        }
    }

    // TxControlReg: 0x83 with the antenna on (after PCD_Init), 0x80 after a reset
    byte PCD_ReadRegister(PCD_Register) {
        if (isReset()) {
            return 0x80;
        }
        if (rfid_stub::event("reset")) {  // right after this check
            rfid_stub::write("RFID_STUB_RESET", "", "w");
        }
        return 0x83;
    }

    // WUPA: true if a tag answers (several: a collision, which the library counts as present)
    virtual bool PICC_IsCardPresent() {
        field();
        if (isReset() || count == 0) {
            note("WUPA none");
            return false;
        }
        if (rfid_stub::event("miss")) {
            note("WUPA miss");
            return false;
        }
        std::string deaf;  // a tag that does not hear this WUPA
        std::string next = rfid_stub::nextEvent();
        if (next.rfind("miss@", 0) == 0) {
            rfid_stub::useEvent();
            deaf = next.substr(5);
        }
        int answers = 0;
        for (int i = 0; i < count; i++) {
            Tag &tag = tags[i];
            if (rfid_stub::hex(tag.uid) == deaf) {
                continue;
            }
            if (tag.state == IDLE || tag.state == HALT) {
                tag.woken = tag.state == HALT;
                tag.state = READY;
                answers++;
            } else {
                fallBack(tag);  // no command for a READY or ACTIVE tag
            }
        }
        if (answers > 0 && rfid_stub::event("garble")) {
            note("WUPA garbled");
            return false;
        }
        note(answers > 0 ? "WUPA answer" : "WUPA none");
        return answers > 0;
    }

    // the select (anticollision): the chosen READY tag becomes ACTIVE, its uid goes to uid
    virtual bool PICC_ReadCardSerial() {
        field();
        int chosen = -1;
        for (int i = 0; i < count; i++) {
            if (tags[i].state == READY && (chosen < 0 || wins(tags[i].uid, tags[chosen].uid))) {
                chosen = i;
            }
        }
        if (chosen < 0 || rfid_stub::event("noselect") || tags[chosen].uid.size() > sizeof(uid.uidByte)) {
            note("SELECT fail");
            return false;
        }
        for (int i = 0; i < count; i++) {
            if (i != chosen && tags[i].state == READY) {
                fallBack(tags[i]);  // the select names another uid
            }
        }
        Tag &tag = tags[chosen];
        tag.state = ACTIVE;
        uid.size = static_cast<byte>(tag.uid.size());
        for (size_t i = 0; i < tag.uid.size(); i++) {
            uid.uidByte[i] = static_cast<byte>(tag.uid[i]);
        }
        uid.sak = 0x00;
        note("SELECT " + rfid_stub::hex(tag.uid));
        return true;
    }

    StatusCode PICC_HaltA() {
        field();
        for (int i = 0; i < count; i++) {
            Tag &tag = tags[i];
            if (tag.state == ACTIVE) {
                tag.state = HALT;
            } else if (tag.state == READY) {
                fallBack(tag);  // HLTA is no command for a READY tag either
            }
        }
        note("HLTA");
        return STATUS_OK;
    }

    void PCD_StopCrypto1() {}

private:
    enum State { IDLE, READY, ACTIVE, HALT };

    struct Tag {
        std::string uid;      // its bytes
        State state = IDLE;   // a tag that comes into the field
        bool woken = false;   // READY or ACTIVE after HALT: falls back to HALT
    };

    Tag tags[rfid_stub::MAX_TAGS];
    int count = 0;

    static bool isReset() { return rfid_stub::exists("RFID_STUB_RESET"); }

    static void note(const std::string &line) { rfid_stub::append("RFID_STUB_LOG", line); }

    static void fallBack(Tag &tag) { tag.state = tag.woken ? HALT : IDLE; }

    // the anticollision: at the first bit in which the uids differ the tag with the bit set wins
    static bool wins(const std::string &a, const std::string &b) {
        for (size_t i = 0; i < a.size() && i < b.size(); i++) {
            int differ = (static_cast<unsigned char>(a[i]) ^ static_cast<unsigned char>(b[i]));
            if (differ != 0) {
                return (static_cast<unsigned char>(a[i]) & (differ & -differ)) != 0;
            }
        }
        return false;
    }

    // the tags in the field now: the ones of RFID_STUB_TAG (a new one is IDLE) - none while the reader is reset
    void field() {
        Tag now[rfid_stub::MAX_TAGS];
        int found = 0;
        std::string text = isReset() ? "" : rfid_stub::read("RFID_STUB_TAG");
        size_t start = 0;
        while (start < text.size() && found < rfid_stub::MAX_TAGS) {
            size_t end = text.find('\n', start);
            std::string bytes = rfid_stub::parseUid(text.substr(start, end - start));
            if (!bytes.empty()) {
                now[found].uid = bytes;
                for (int i = 0; i < count; i++) {
                    if (tags[i].uid == bytes) {
                        now[found] = tags[i];
                    }
                }
                found++;
            }
            start = end == std::string::npos ? text.size() : end + 1;
        }
        for (int i = 0; i < found; i++) {
            tags[i] = now[i];
        }
        count = found;
    }
};

#endif  // MFRC522_STUB_H
