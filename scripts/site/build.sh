#!/usr/bin/env bash
# The documentation site (docs/developer/development.md, section 10): assembled by assemble.py into
# build/site-src, built by MkDocs into build/site. MkDocs and Material run with uvx in pinned versions – they are
# no dependency of the box or the library. --strict: a broken link or anchor fails the build.
#
#   scripts/site/build.sh          build into build/site
#   scripts/site/build.sh serve    a preview on http://127.0.0.1:8000 that follows the assembled files
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 scripts/site/assemble.py
MKDOCS=(uvx --with mkdocs-material==9.6.23 --from mkdocs==1.6.1 mkdocs)
if [[ "${1:-}" == "serve" ]]; then
    exec "${MKDOCS[@]}" serve -f build/site-src/mkdocs.yml
fi
"${MKDOCS[@]}" build --strict -f build/site-src/mkdocs.yml
echo "built: build/site"
