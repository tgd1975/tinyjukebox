"""The help of the web pages of box and library (docs/developer/rest-api.md, section 5): the documents of users and
administrators, docs/user/<language>/{quick-guide,manual,admin}.md, as HTML pages in src/tinyjukebox/web/web/help/.
The box serves them itself, without the internet, in the version it runs.

    uv run python scripts/help/build.py          writes the pages (after a change of a document of docs/user)
    uv run python scripts/help/build.py --check  fails if the pages are not those of the documents (test_help.py)

The pages are generated and checked in, as docs/config_schema.json: the box needs no Markdown. A link between the
documents stays in the help, a link to a developer document goes to the documentation site, a picture to its copy in
help/images/ (a JPEG at most 640 px wide, made once by hand: `convert docs/images/X.jpg -strip -resize '640x640>'
-quality 82 src/tinyjukebox/web/web/help/images/X.jpg`; an SVG is copied by this script).

Every numbered heading carries an anchor of its number as well - `#s7-2` for 7.2 - that is the same in every
language: the page of the box opens the help at the chapter of its tab with it (app.js, HELP_OF_TAB).
"""

from __future__ import annotations

import html
import importlib.util
import posixpath
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "tinyjukebox" / "web" / "web"
OUT = WEB / "help"
DOCUMENTS = {  # file → its name in the bar of the help, per language
    "quick-guide": {"en": "Quick guide", "de": "Kurzanleitung"},
    "manual": {"en": "Manual", "de": "Handbuch"},
    "admin": {"en": "Administration", "de": "Verwaltung"},
}
LANGUAGES = {"en": "English", "de": "Deutsch"}
BACK = {"en": "Back to the page", "de": "Zurück zur Seite"}
HEAD = {"en": "Help", "de": "Hilfe"}

spec = importlib.util.spec_from_file_location("assemble", ROOT / "scripts" / "site" / "assemble.py")
if spec is None or spec.loader is None:
    sys.exit("scripts/site/assemble.py is missing")
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


def site_pages():
    """{repository file: its address on the documentation site} - where a link to a developer document goes."""
    out = {}
    for src, path, *_ in site.pages():
        if src:
            out.setdefault(src, site.SITE + site.url_of(path))
    return out


SITE_PAGES = site_pages()


def target(link, language):
    """Where a link of docs/user/<language>/ goes in the help; None for one that stays as it is."""
    if re.match(r"[a-z][a-z0-9+.-]*:|//|#", link):
        return None
    path, _, anchor = link.partition("#")
    rel = posixpath.normpath(posixpath.join(f"docs/user/{language}", path))
    hash_ = f"#{anchor}" if anchor else ""
    if rel.startswith(f"docs/user/{language}/") and Path(rel).stem in DOCUMENTS:
        return f"{Path(rel).stem}.html{hash_}"
    if rel.startswith("docs/images/"):
        name = posixpath.basename(rel)
        if not (OUT / "images" / name).is_file() and not name.endswith(".svg"):
            sys.exit(f"{rel}: no copy in {OUT.relative_to(ROOT)}/images/ (see the docstring of this script)")
        return f"../images/{name}"
    if rel in SITE_PAGES:
        return SITE_PAGES[rel] + hash_
    return f"{site.REPO}/blob/{site.BRANCH}/{rel}{hash_}"


def prepare(text, language):
    """The Markdown as Python-Markdown reads it: the links of the help, nested lists indented by four spaces, no
    comments."""
    text = re.sub(r"<!--.*?-->\n?", "", text, flags=re.S)
    # GitHub takes a list in a list item at the indent of the item's text; Python-Markdown wants four spaces
    text = re.sub(r"^ {2,3}(?=(?:[-*]|\d+\.) )", "    ", text, flags=re.M)

    def markdown_link(m):
        new = target(m.group(2), language)
        return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}"

    def tag_link(m):
        new = target(m.group(2), language)
        return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}"

    parts = re.split(r"(^```.*?^```[ \t]*$)", text, flags=re.M | re.S)
    return "".join(
        p if p.startswith("```") else site.ATTR.sub(tag_link, site.LINK.sub(markdown_link, p)) for p in parts
    )


def number_anchors(body):
    """An anchor of its number in every numbered heading: <h3 id="72-…"><span id="s7-2"></span>7.2 …."""

    def add(m):
        number = m.group(3).rstrip(".")
        return f'{m.group(1)}<span id="s{number.replace(".", "-")}"></span>{m.group(3)}'

    return re.sub(r'(<h([1-6]) id="[^"]*">)(\d+(?:\.\d+)*\.?)(?= )', add, body)


def brand():
    """The lid and the name, as in the top bar of the page (index.html is the source)."""
    index = (WEB / "index.html").read_text(encoding="utf-8")
    return re.search(r'<svg class="brand-logo".*?</svg>', index, re.S).group(0)


def page(document, language, body, title):
    tabs = "".join(
        f'<a href="{name}.html"{" class=active aria-current=page" if name == document else ""}>{d[language]}</a>'
        for name, d in DOCUMENTS.items()
    )
    other = next(lang for lang in LANGUAGES if lang != language)
    return f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{html.escape(title)} – Tiny Jukebox</title>
<link rel="icon" href="../../favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="../../style.css">
<link rel="stylesheet" href="../help.css">
</head>
<body class="help">
<!-- made by scripts/help/build.py from docs/user/{language}/{document}.md - not to be changed by hand -->
<header class="topbar">
  <a class="brand" href="../../">{brand()}Tiny Jukebox</a>
  <span class="muted small">{HEAD[language]}</span>
  <nav class="tabs help-tabs">{tabs}</nav>
  <div class="user"><a href="../{other}/{document}.html" hreflang="{other}" lang="{other}">{LANGUAGES[other]}</a>
    <a class="button" href="../../">{BACK[language]}</a></div>
</header>
<main class="help-text">
{body}
</main>
</body>
</html>
"""


def build(out=OUT):
    """{path below out: content} of every page, and the pages written into out."""
    files = {}
    for language in LANGUAGES:
        for document in DOCUMENTS:
            text = (ROOT / "docs" / "user" / language / f"{document}.md").read_text(encoding="utf-8")
            title = re.search(r"^# (.+)$", text, re.M).group(1)
            converter = markdown.Markdown(
                extensions=["tables", "fenced_code", "md_in_html", "toc", "sane_lists"],
                extension_configs={"toc": {"slugify": lambda value, separator: site.slug(value)}},
            )
            body = number_anchors(converter.convert(prepare(text, language)))
            files[f"{language}/{document}.html"] = page(document, language, body, title)
    for svg in sorted({m for f in files.values() for m in re.findall(r"\.\./images/([\w.-]+\.svg)", f)}):
        files[f"images/{svg}"] = (ROOT / "docs" / "images" / svg).read_text(encoding="utf-8")
    for name, content in files.items():
        path = Path(out) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
    return files


def check():
    """The names of the pages that are not those of the documents."""
    stale = []
    for name, content in build_in_memory().items():
        path = OUT / name
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            stale.append(name)
    return stale


def build_in_memory():
    tmp = Path(__import__("tempfile").mkdtemp())
    try:
        shutil.copytree(OUT / "images", tmp / "images")
        return build(tmp)
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    if "--check" in sys.argv:
        stale = check()
        if stale:
            sys.exit(f"the help is not that of docs/user - run scripts/help/build.py: {', '.join(stale)}")
        print("the help is current")
    else:
        print(f"written: {len(build())} files below {OUT.relative_to(ROOT)}")
