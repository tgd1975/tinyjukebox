# Builds rfid-reader from its source and the sources of the RC522 library.
#
#   make MFRC522_DIR=/srv/tinyjukebox/cpp/RPi-MFRC522 && sudo make install
#
# MFRC522_DIR: clone of https://github.com/tgd1975/RPi-MFRC522 (the fork with PICC_IsCardPresent).
# The bcm2835 library must be installed (http://www.airspayce.com/mikem/bcm2835/).
#
# The tests build src/main.cpp with CXXFLAGS and WARNINGS of this file and -Werror, against tests/stub/MFRC522.h
# (tests/devices/integration/test_rfid_reader.py).

MFRC522_DIR ?= ../RPi-MFRC522
PREFIX ?= /usr/local
CXXFLAGS ?= -O2
WARNINGS = -Wall -Wextra

# wildcard: without the library the recipe says so, instead of make stopping at a missing source
LIBRARY = $(wildcard $(MFRC522_DIR)/src/MFRC522.cpp $(MFRC522_DIR)/src/RPi-Arduino-SPI/*.cpp)
HEADERS = $(wildcard $(MFRC522_DIR)/src/*.h $(MFRC522_DIR)/src/RPi-Arduino-SPI/*.h)
INCLUDES = -I$(MFRC522_DIR)/src -I$(MFRC522_DIR)/src/RPi-Arduino-SPI

rfid-reader: src/main.cpp $(LIBRARY) $(HEADERS) Makefile
	@test -f $(MFRC522_DIR)/src/MFRC522.h || { echo "MFRC522_DIR=$(MFRC522_DIR): no RPi-MFRC522 found" >&2; exit 1; }
	$(CXX) -std=c++17 $(CXXFLAGS) $(WARNINGS) $(INCLUDES) src/main.cpp $(LIBRARY) -lbcm2835 -o $@

install: rfid-reader
	install -m 755 rfid-reader $(PREFIX)/bin/rfid-reader

clean:
	rm -f rfid-reader

.PHONY: install clean
