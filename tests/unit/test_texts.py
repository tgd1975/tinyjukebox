"""The texts of the web page: one file per language (web/web/texts), which index.html and app.js only name by keys."""

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tinyjukebox.web import server

WEB = Path(server.WEB_FOLDER)
PLACES = ("data-t", "data-t-title", "data-t-label", "data-t-placeholder", "data-t-confirm")
ATTRIBUTES = ("title", "aria-label", "placeholder", "data-confirm")  # what the page fills from data-t-...
FORMS = {"zero", "one", "two", "few", "many", "other"}  # the forms of a number (Intl.PluralRules)
EMPHASIS = re.compile(r"</?(\w+)[^>]*>")
VALUE = re.compile(r"\{(\w+)\}")
DEFINITIONS = {"key", "el.dataset.t", "el.getAttribute(`data-t-${name}`)"}  # t() within the text functions


class Page(HTMLParser):
    """index.html: the elements that name a text, with the parts they keep, and whatever text it has itself."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[tuple[str, dict[str, str | None]]] = []
        self.keys: dict[str, set[str]] = {place: set() for place in PLACES}
        self.parts: dict[str, set[str]] = {}
        self.own: list[str] = []
        self.feed((WEB / "index.html").read_text(encoding="utf-8"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for place in PLACES:
            if attributes.get(place):
                self.keys[place].add(str(attributes[place]))
        if attributes.get("data-t"):
            self.parts.setdefault(str(attributes["data-t"]), set())
        if attributes.get("data-part") and self.open and self.open[-1][1].get("data-t"):
            self.parts[str(self.open[-1][1]["data-t"])].add(str(attributes["data-part"]))
        self.own += [f"{name}={attributes[name]}" for name in ATTRIBUTES if attributes.get(name)]
        if tag not in {"meta", "link", "input", "br", "img", "hr"}:
            self.open.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        while self.open and self.open.pop()[0] != tag:
            pass

    def handle_data(self, data: str) -> None:
        if re.search(r"[^\W\d_]{2,}", data) and self.open and self.open[-1][0] not in {"script", "style"}:
            self.own.append(" ".join(data.split()))


@pytest.fixture(scope="module")
def texts() -> dict[str, dict[str, str | dict[str, str]]]:
    return {file.stem: json.loads(file.read_text(encoding="utf-8")) for file in sorted((WEB / "texts").glob("*.json"))}


@pytest.fixture(scope="module")
def page() -> Page:
    return Page()


@pytest.fixture(scope="module")
def script() -> str:
    code = (WEB / "app.js").read_text(encoding="utf-8")
    return re.sub(r"^\s*//.*$", "", code, flags=re.MULTILINE)


def _asked(script: str) -> tuple[set[str], set[str]]:
    """The keys app.js asks for with t() and the groups it asks for with tOf()."""
    keys = set(re.findall(r'\bt\(\s*"([\w.]+)"', script))
    for one, other in re.findall(r'\bt\(\s*[^",()]+\?\s*"([\w.]+)"\s*:\s*"([\w.]+)"', script):
        keys |= {one, other}
    return keys, set(re.findall(r'\btOf\("([\w.]+)"', script))


def _variants(text: str | dict[str, str]) -> list[str]:
    return list(text.values()) if isinstance(text, dict) else [text]


def test_languages_have_the_same_texts(texts):
    """Every key in every language, with the same values in braces and the same emphasis."""
    reference = texts["de"]
    assert reference
    for language, own in texts.items():
        assert set(own) == set(reference), f"{language}: {sorted(set(own) ^ set(reference))}"
        for key, text in own.items():
            assert isinstance(text, dict) == isinstance(reference[key], dict), f"{language}: {key}"
            if isinstance(text, dict):
                assert "other" in text, f"{language}: {key}"
                assert set(text) <= FORMS, f"{language}: {key}"
            for variant in _variants(text):
                assert variant.strip(), f"{language}: {key} is empty"
                assert set(VALUE.findall(variant)) == set(VALUE.findall(_variants(reference[key])[-1])), (
                    f"{language}: {key}"
                )
                assert sorted(EMPHASIS.findall(variant)) == sorted(EMPHASIS.findall(_variants(reference[key])[-1])), key


def test_keys_are_known_and_used(texts, page, script):
    """A key that is asked for exists, and a text that nobody asks for is not kept."""
    keys, groups = _asked(script)
    for place in PLACES:
        keys |= page.keys[place]
    for language, own in texts.items():
        assert not keys - set(own), f"{language} lacks {sorted(keys - set(own))}"
        unused = {key for key in own if key not in keys and not any(key.startswith(f"{group}.") for group in groups)}
        assert not unused, f"{language}: nobody asks for {sorted(unused)}"
    for group in groups:
        assert any(key.startswith(f"{group}.") for key in texts["de"]), f"the group {group} has no text"


def test_keys_are_written_out(script):
    """t() is called with the key itself or with a choice of two keys - only then the test above sees every key."""
    for argument in re.findall(r"\bt\(\s*([^\"\s][^,]*?)\s*[,)]", script):
        if argument in DEFINITIONS or argument.startswith("el.getAttribute"):
            continue
        assert re.fullmatch(r'[^"?]+\?\s*"[\w.]+"\s*:\s*"[\w.]+"', argument), f"t({argument} ...)"
    assert not re.findall(r"(?<!function )\btOf\(\s*[^\"\s]", script), "tOf() takes its group as it is written"


def test_the_page_has_no_text_of_its_own(page, script):
    """index.html has the name of the project and nothing else; app.js builds no text from German words."""
    assert set(page.own) == {"Tiny Jukebox"}, page.own
    assert not re.findall(r"[\"`][^\"`\n]*[äöüÄÖÜß„“][^\"`\n]*[\"`]", script)


def test_fixed_texts(texts, page, script):
    """A fixed text takes no values - only the elements its place in index.html keeps. Emphasis is for fixed texts."""
    asked, _ = _asked(script)
    for language, own in texts.items():
        for key, parts in page.parts.items():
            assert set(VALUE.findall(str(own[key]))) == parts, f"{language}: {key}"
        for key, text in own.items():
            tags = {tag for variant in _variants(text) for tag in EMPHASIS.findall(variant)}
            if tags:
                assert tags <= {"b", "code", "strong"}, f"{language}: {key}"
                assert key in page.keys["data-t"], f"{language}: {key} is not a fixed text"
                assert key not in asked, f"{language}: {key} is also asked for by app.js"


def _codes() -> dict[str, list[set[str]]]:
    """The codes of the errors in the Python sources, each with the values every place gives along with it."""
    codes: dict[str, list[set[str]]] = {}
    for file in sorted(Path(server.__file__).parents[1].rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                given = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
                code = given.get("code")
                if isinstance(code, ast.Constant) and isinstance(code.value, str) and "." in code.value:
                    codes.setdefault(code.value, []).append(set(given) - {"code"})
            elif isinstance(node, ast.Dict):
                entries = {
                    key.value: value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant)
                }
                code = entries.get("code")
                if "error" in entries and isinstance(code, ast.Constant):
                    codes.setdefault(str(code.value), []).append(set())
    return codes


def test_errors_with_a_code_have_a_text(texts):
    """The page has a text for every code the server gives an error, with no value the server does not send."""
    codes = _codes()
    assert len(codes) > 40
    for language, own in texts.items():
        said = {key.removeprefix("error.") for key in own if key.startswith("error.")} - {"status"}
        assert said == set(codes), f"{language}: {sorted(said ^ set(codes))}"
        for code, places in codes.items():
            for given in places:
                assert set(VALUE.findall(str(own[f"error.{code}"]))) <= given, f"{language}: error.{code}"


def test_languages_of_the_page(texts, script):
    """The page offers exactly the languages that have a text file, and the server delivers every one of them."""
    languages = re.search(r"const LANGUAGES = \{([^}]*)\};", script)
    assert languages
    assert set(re.findall(r"(\w+):", languages.group(1))) == set(texts)
    for language in texts:
        filename, content_type = server.STATIC_FILES[f"/texts/{language}.json"]
        assert (WEB / filename).is_file()
        assert content_type.startswith("application/json")
