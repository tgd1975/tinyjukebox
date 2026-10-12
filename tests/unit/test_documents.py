"""The user documents (docs/user/<language>): every language has the same files with the same structure."""

import re
from pathlib import Path

import pytest

USER = Path(__file__).parents[2] / "docs" / "user"
LANGUAGES = sorted(folder.name for folder in USER.iterdir() if folder.is_dir())
DOCUMENTS = sorted(file.name for file in (USER / "de").glob("*.md"))
HEADING = re.compile(r"^(#+) (\d+(?:\.\d+)*\.? )?")
LINK = re.compile(r"\]\(([^)#][^)]*?)(?:#[^)]*)?\)")


def _structure(text: str) -> list[tuple[str, int, int, int]]:
    """Per section: its level and number, and how many table rows, list items and numbered steps it has."""
    sections: list[list] = [["", 0, 0, 0]]
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            sections.append([f"{heading.group(1)} {(heading.group(2) or '').strip()}", 0, 0, 0])
        elif line.startswith("|"):
            sections[-1][1] += 1
        elif re.match(r"\s*- ", line):
            sections[-1][2] += 1
        elif re.match(r"\s*\d+\. ", line):
            sections[-1][3] += 1
    return [tuple(section) for section in sections]


def test_languages_and_documents():
    assert LANGUAGES == ["de", "en"]
    assert DOCUMENTS == ["manual.md", "quick-guide.md"]
    for language in LANGUAGES:
        assert sorted(file.name for file in (USER / language).iterdir()) == DOCUMENTS, language


@pytest.mark.parametrize("document", DOCUMENTS)
def test_same_structure_in_every_language(document):
    """A section, a table row or a list item that was added in one language only is found here."""
    reference = _structure((USER / "de" / document).read_text(encoding="utf-8"))
    assert len(reference) > 5
    for language in LANGUAGES:
        own = _structure((USER / language / document).read_text(encoding="utf-8"))
        assert [section[0] for section in own] == [section[0] for section in reference], language
        for section, expected in zip(own, reference, strict=True):
            assert section == expected, f"{language}/{document}: section '{section[0]}' (rows, items, steps)"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("document", DOCUMENTS)
def test_links_lead_somewhere(language, document):
    folder = USER / language
    for target in LINK.findall((folder / document).read_text(encoding="utf-8")):
        if not target.startswith(("http://", "https://")):
            assert (folder / target).exists(), f"{language}/{document}: {target}"
