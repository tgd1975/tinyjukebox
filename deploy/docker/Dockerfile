# The image of Tiny Jukebox (docs/developer/library.md, section 3.9): the library, or a box without hardware and
# without sound. Built by scripts/build_image.sh from what the workflow built - the wheel of this version, the
# versions of its dependencies from uv.lock and the slot for the boxes - not from the source tree: the image and
# PyPI hold the same files.
# The base image is pinned to its digest (both platforms); dependabot proposes the next one (.github/dependabot.yml)
FROM python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
# the registry of GitHub links the image to the repository: it has its visibility
LABEL org.opencontainers.image.source=https://github.com/tgd1975/tinyjukebox
LABEL org.opencontainers.image.description="Tiny Jukebox: the library, or a box without hardware"
LABEL org.opencontainers.image.licenses=MIT

# ffmpeg: the player of a box needs it, also without sound; the library carries it along (one image for both)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
COPY tinyjukebox-*.whl /tmp/
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt \
    && pip install --no-cache-dir --disable-pip-version-check --no-deps /tmp/tinyjukebox-*.whl \
    && rm /tmp/requirements.txt /tmp/tinyjukebox-*.whl

# the slot of this version for the boxes, as the package for the NAS brings it: the library can hand a version to a
# box before it has asked the releases, and with it the installer of a box
COPY slots/ /usr/local/share/tinyjukebox/slots/
ENV TINYJUKEBOX_SLOTS=/usr/local/share/tinyjukebox/slots

COPY entrypoint.sh /usr/local/bin/tinyjukebox-entrypoint
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin tinyjukebox \
    && mkdir /data && chown tinyjukebox:tinyjukebox /data
USER tinyjukebox
VOLUME /data
EXPOSE 8866 8443

# the page answers GET /api/identity without a login: the library on 8866, a box on 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD ["python3", "-c", "import ssl, sys, urllib.request\ncontext = ssl._create_unverified_context()\nfor port in (8866, 8443):\n    try:\n        urllib.request.urlopen(f'https://localhost:{port}/api/identity', context=context, timeout=4)\n        sys.exit(0)\n    except OSError:\n        pass\nsys.exit(1)"]

ENTRYPOINT ["/usr/local/bin/tinyjukebox-entrypoint"]
CMD ["library"]
