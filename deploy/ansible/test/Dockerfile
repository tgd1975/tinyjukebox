# A stand-in for a box, for trying the playbook role "slots" and the start script under systemd without touching a
# real box: Debian trixie with systemd as the first process, the user pi with sudo, and what the playbook expects of
# the roles before "slots" (python for ansible, the folder of the box). Used by run.sh.
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus sudo python3 python3-apt procps \
        ca-certificates libasound2t64 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash pi \
    && echo 'pi ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/010_pi-nopasswd \
    && systemctl mask getty.target console-getty.service systemd-logind.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
