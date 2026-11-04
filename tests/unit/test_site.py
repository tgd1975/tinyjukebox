"""The documentation site (scripts/site/assemble.py, docs/developer/development.md, section 10): every
document is on it or left out on purpose, every chapter of a manual on exactly one page, the tabs short, and every link
of the assembled pages leads to a page, an anchor or a file of the site. Without MkDocs: the script is plain Python;
the workflow site.yml builds the site itself with --strict."""

import importlib.util
import json
import posixpath
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("assemble", ROOT / "scripts" / "site" / "assemble.py")
assert SPEC
assert SPEC.loader
site = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site)
SKIP_DIRS = {"build", "dist", "node_modules"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = site.assemble(tmp_path_factory.mktemp("site") / "src")
    return out / "docs"


def documents():
    """Every Markdown file of the repository, without the folders of tools and builds."""
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*.md")
        if not any(part.startswith(".") or part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    )


def test_every_document_is_on_the_site_or_left_out_on_purpose():
    on_site = {src for src, *_ in site.pages() if src}
    missing = [d for d in documents() if d not in on_site and d not in site.LEFT_OUT]
    assert not missing, f"neither in NAV of scripts/site/assemble.py nor in LEFT_OUT: {missing}"
    assert not [d for d in site.LEFT_OUT if d in on_site]


def test_tabs_are_short():
    for tab in site.nav(site.pages()):
        ((name, entries),) = tab.items()
        assert len(entries) <= site.SIDEBAR, f"tab {name!r} has {len(entries)} entries"


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_chapter_of_a_manual_is_on_exactly_one_page(language):
    _, chapters = site.chapters((ROOT / f"docs/user/{language}/manual.md").read_text(encoding="utf-8"))
    placed = [n for *_, numbers in site.MANUAL_PAGES for n in numbers]
    assert sorted(placed) == sorted(chapters), "MANUAL_PAGES must place every chapter of the manual once"


def test_the_kinds_of_playing_are_pages_of_their_own():
    names = [name for name, *_ in site.MANUAL_PAGES]
    for name in ("mp3-albums", "radio", "spotify"):
        assert name in names


def test_a_page_of_use_leads_to_its_german_page(built):
    for page in (built / "use").glob("*.md"):
        text = page.read_text(encoding="utf-8")
        assert f"(../bedienen/{page.name})" in text, page.name
        german = (built / "bedienen" / page.name).read_text(encoding="utf-8")
        assert german.startswith('<div lang="de" markdown>')
        assert f"(../use/{page.name})" in german


def test_concepts_are_marked_as_plans(built):
    for page in (built / "concepts").glob("*.md"):
        if page.name != "index.md":
            assert '!!! note "A plan"' in page.read_text(encoding="utf-8"), page.name


def test_the_configuration_keys_name_every_key_of_the_schema(built):
    schema = json.loads((ROOT / "docs/config_schema.json").read_text(encoding="utf-8"))
    text = (built / "reference" / "index.md").read_text(encoding="utf-8")
    for key in schema["properties"]["tinyjukebox"]["properties"]:
        assert f"`tinyjukebox.{key}`" in text, key


def anchors_of(path, cache={}):  # noqa: B006 - a cache for the test run
    if path not in cache:
        text = path.read_text(encoding="utf-8")
        cache[path] = {s for _, s in site.headings(text)} | set(re.findall(r'\bid="([^"]+)"', text))
    return cache[path]


def page_of_url(docs, page, url):
    """The Markdown file behind a directory URL of an HTML link, relative to the page's own URL."""
    base = site.url_of(page.relative_to(docs).as_posix())
    target = posixpath.normpath(posixpath.join(base, url)) if url not in ("", ".", "./") else base.rstrip("/") or "."
    target = "" if target == "." else target.strip("/")
    for candidate in (f"{target}.md", f"{target}/index.md" if target else "index.md"):
        if (docs / candidate).is_file():
            return docs / candidate
    return docs / target


def test_every_link_of_the_site_leads_somewhere(built):
    broken = []
    for page in sorted(built.rglob("*.md")):
        text = site.FENCE.sub("", page.read_text(encoding="utf-8"))
        links = [(m.group(2), False) for m in site.LINK.finditer(text)]
        links += [(m.group(2), True) for m in site.ATTR.finditer(text)]
        for target, html in links:
            if re.match(r"[a-z][a-z0-9+.-]*:|//", target):
                continue
            path, _, anchor = target.partition("#")
            if not path:
                goal = page
            elif html:  # an HTML link is read from the page's URL, a directory
                base = site.url_of(page.relative_to(built).as_posix())
                goal = built / posixpath.normpath(posixpath.join(base, path))
                goal = goal if goal.is_file() else page_of_url(built, page, path)
            else:
                goal = built / posixpath.normpath(posixpath.join(page.parent.relative_to(built).as_posix(), path))
            if not goal.exists():
                broken.append(f"{page.relative_to(built)}: {target}")
            elif anchor and goal.suffix == ".md" and anchor not in anchors_of(goal):
                broken.append(f"{page.relative_to(built)}: {target} (no such anchor)")
    assert not broken, "\n".join(broken)
