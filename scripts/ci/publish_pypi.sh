#!/bin/bash
# publishes wheel and source archive of this version to PyPI, after the release and the image (docs/developer/
# library.md, section 8.2). PyPI trusts the workflow of this repository ("Trusted Publishing", id-token: write): no
# password is stored. A file that PyPI has already is skipped: the job can be run again, and a version on PyPI is final.
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
uv publish --trusted-publishing always --check-url https://pypi.org/simple/ \
    "dist/tinyjukebox-$VERSION-py3-none-any.whl" "dist/tinyjukebox-$VERSION.tar.gz"
