"""The help of the web pages (scripts/help/build.py): the pages are those of the documents of docs/user, every link of
the page into the help finds its chapter in both languages, and the box serves every file of the help."""

import importlib.util
import re
from pathlib import Path

import pytest

from tinyjukebox.web import server

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "tinyjukebox" / "web" / "web"
HELP = WEB / "help"
SPEC = importlib.util.spec_from_file_location("help_build", ROOT / "scripts" / "help" / "build.py")
assert SPEC
assert SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_the_help_is_that_of_the_documents():
    assert not build.check(), "run: uv run python scripts/help/build.py"


def help_of_tab():
    text = (WEB / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const HELP_OF_TAB = \{(.*?)\};", text, re.S)
    assert block, "app.js has no HELP_OF_TAB"
    return dict(re.findall(r'"?([\w-]+)"?: "([\w-]+#[\w-]+)"', block.group(1)))


def test_every_tab_has_its_help():
    tabs = set(re.findall(r'data-tab="([\w-]+)"', (WEB / "index.html").read_text(encoding="utf-8")))
    assert tabs == set(help_of_tab())


@pytest.mark.parametrize("language", sorted(build.LANGUAGES))
def test_the_help_of_a_tab_finds_its_chapter(language):
    for tab, page in help_of_tab().items():
        document, anchor = page.split("#")
        text = (HELP / language / f"{document}.html").read_text(encoding="utf-8")
        assert f'id="{anchor}"' in text, f"{tab}: {language}/{document}.html has no #{anchor}"


@pytest.mark.parametrize("page", sorted(p.relative_to(HELP).as_posix() for p in HELP.glob("*/*.html")))
def test_every_link_inside_the_help_leads_somewhere(page):
    path = HELP / page
    text = path.read_text(encoding="utf-8")
    for link in re.findall(r'(?:href|src)="([^"]+)"', text):
        if link.startswith(("https://", "../../")):
            continue
        file, _, anchor = link.partition("#")
        target = (path.parent / file).resolve() if file else path
        assert target.is_file(), f"{page}: {link}"
        if anchor and target.suffix == ".html":
            assert f'id="{anchor}"' in target.read_text(encoding="utf-8"), f"{page}: {link}"


def test_the_help_loads_nothing_from_elsewhere():
    for path in HELP.glob("*/*.html"):
        assert not re.search(r'(?:src|<link[^>]*href)="https?:', path.read_text(encoding="utf-8")), path


def test_the_box_serves_every_file_of_the_help():
    files = {p.relative_to(HELP).as_posix() for p in HELP.rglob("*") if p.is_file()}
    assert {f"/help/{f}" for f in files} <= set(server.STATIC_FILES)
