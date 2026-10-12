#!/bin/bash
# installs and enables the systemd service (run on the box, from any folder)
set -e
cd "$(dirname "$0")"
sudo cp tinyjukebox.service /lib/systemd/system/
sudo chmod 644 /lib/systemd/system/tinyjukebox.service
sudo systemctl daemon-reload
sudo systemctl enable tinyjukebox.service
