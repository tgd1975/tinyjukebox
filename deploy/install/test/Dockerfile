# A stand-in for a box, for trying the installer (deploy/install/install.sh) and the start script under systemd
# without touching a real box: Debian trixie with systemd as the first process, the user pi with sudo, and the
# config.txt of Raspberry Pi OS that the installer expects, and the package source of Raspberry Pi OS next to Debian
# (liblgpio1 is only there). Used by run.sh.
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
# the key of the package source as Raspberry Pi OS has it (raspberrypi-archive-keyring); the older key file of the
# archive is bound with SHA1, which apt of trixie refuses
ADD http://archive.raspberrypi.com/debian/pool/main/r/raspberrypi-archive-keyring/raspberrypi-archive-keyring_2025.1+rpt1_all.deb /tmp/keyring.deb
RUN dpkg -i /tmp/keyring.deb && rm /tmp/keyring.deb \
    && printf 'Types: deb\nURIs: http://archive.raspberrypi.com/debian/\nSuites: trixie\nComponents: main\nSigned-By: /usr/share/keyrings/raspberrypi-archive-keyring.pgp\n' \
        > /etc/apt/sources.list.d/raspi.sources
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus sudo procps ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash pi \
    && echo 'pi ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/010_pi-nopasswd \
    && mkdir -p /boot/firmware && printf 'dtparam=audio=on\ncamera_auto_detect=1\n' > /boot/firmware/config.txt \
    && systemctl mask getty.target console-getty.service systemd-logind.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
