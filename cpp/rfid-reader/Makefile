# Builds rfid-reader from its source and the sources of the RC522 library.
#
#   make MFRC522_DIR=/srv/tinyjukebox/cpp/RPi-MFRC522 && sudo make install
#
# MFRC522_DIR: clone of https://github.com/tgd1975/RPi-MFRC522 (the fork with PICC_IsCardPresent).
# The bcm2835 library must be installed (http://www.airspayce.com/mikem/bcm2835/).

MFRC522_DIR ?= ../RPi-MFRC522
PREFIX ?= /usr/local
CXXFLAGS ?= -O2

LIBRARY = $(MFRC522_DIR)/src/MFRC522.cpp $(wildcard $(MFRC522_DIR)/src/RPi-Arduino-SPI/*.cpp)
INCLUDES = -I$(MFRC522_DIR)/src -I$(MFRC522_DIR)/src/RPi-Arduino-SPI

rfid-reader: src/main.cpp $(LIBRARY)
	@test -f $(MFRC522_DIR)/src/MFRC522.h || { echo "MFRC522_DIR=$(MFRC522_DIR): no RPi-MFRC522 found"; exit 1; }
	$(CXX) -std=c++17 $(CXXFLAGS) $(INCLUDES) src/main.cpp $(LIBRARY) -lbcm2835 -o $@

install: rfid-reader
	install -m 755 rfid-reader $(PREFIX)/bin/rfid-reader

clean:
	rm -f rfid-reader

.PHONY: install clean
