# A stand-in for a box, for trying the installer (deploy/install/install.sh) and the start script under systemd
# without touching a real box: Debian trixie with systemd as the first process, the user pi with sudo, and the
# config.txt of Raspberry Pi OS that the installer expects. Used by run.sh.
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus sudo procps ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash pi \
    && echo 'pi ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/010_pi-nopasswd \
    && mkdir -p /boot/firmware && printf 'dtparam=audio=on\ncamera_auto_detect=1\n' > /boot/firmware/config.txt \
    && systemctl mask getty.target console-getty.service systemd-logind.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
