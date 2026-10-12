"""tinyjukebox-import-cards: slide geometry of a .pptx (built in tmp_path), grid assignment and the import run
(LibreOffice and pdftoppm are replaced - the tests need neither)."""

import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tinyjukebox.tools import import_cards
from tinyjukebox.tools.import_cards import cards_of_slide, main, read_presentation, slug

NAMESPACES = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
)
W, H = 9144000, 4572000  # slide size in EMU: 10 x 5 inch
CW, CH = W // 2, H // 2  # a cell of the 2x2 grid


def xfrm(x: int, y: int, cx: int, cy: int, children: str = "") -> str:
    return f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>{children}</a:xfrm>'


def pic(x: int, y: int, cx: int, cy: int) -> str:
    return f"<p:pic><p:nvPicPr/><p:blipFill/><p:spPr>{xfrm(x, y, cx, cy)}</p:spPr></p:pic>"


def text(x: int, y: int, cx: int, cy: int, *paragraphs: str | list[str]) -> str:
    """A text shape - a paragraph is a text or a list of runs."""
    body = "".join(
        "<a:p>" + "".join(f"<a:r><a:t>{run}</a:t></a:r>" for run in ([p] if isinstance(p, str) else p)) + "</a:p>"
        for p in paragraphs
    )
    return f"<p:sp><p:nvSpPr/><p:spPr>{xfrm(x, y, cx, cy)}</p:spPr><p:txBody>{body}</p:txBody></p:sp>"


def group(position: tuple[int, int, int, int], child_position: tuple[int, int, int, int] | None, *shapes: str) -> str:
    """A group: ``shapes`` in child coordinates (``child_position``) are scaled into ``position``."""
    children = ""
    if child_position is not None:
        x, y, cx, cy = child_position
        children = f'<a:chOff x="{x}" y="{y}"/><a:chExt cx="{cx}" cy="{cy}"/>'
    return f"<p:grpSp><p:nvGrpSpPr/><p:grpSpPr>{xfrm(*position, children)}</p:grpSpPr>{''.join(shapes)}</p:grpSp>"


def slide(*shapes: str) -> str:
    return (
        f"<p:sld {NAMESPACES}><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{''.join(shapes)}</p:spTree></p:cSld></p:sld>"
    )


SLIDES = {
    # cell a: picture with a two line title, cell b: text only (no card), cell d: picture without text
    "slide1.xml": slide(
        pic(0, 0, CW, CH),
        text(0, CH - 500000, CW, 400000, "  Der kleine ", "Drache"),
        text(CW, 0, CW, CH, "Nur Text"),
        text(CW, CH, 1000, 1000, " "),  # empty text shapes are ignored
        pic(CW, CH, CW, CH),
    ),
    # cell c: a group with picture and title (child coordinates are twice the slide coordinates)
    "slide2.xml": slide(
        group(
            (0, CH, CW, CH),
            (1000, 2000, 2 * CW, 2 * CH),
            pic(1000, 2000, 2 * CW, 2 * CH),
            text(1000, 2000 + 2 * CH - 400000, 2 * CW, 400000, ["Rit", "ter"]),
        ),
        group((0, 0, CW, CH), None, pic(0, 0, CW, CH)),  # groups without child offset/extent are ignored
        "<p:grpSp><p:grpSpPr/>" + pic(0, 0, CW, CH) + "</p:grpSp>",  # ... as well as groups without position
        "<p:sp><p:spPr/><p:txBody><a:p><a:r><a:t>ohne Position</a:t></a:r></a:p></p:txBody></p:sp>",
    ),
    # the third slide (numerically after slide 2): cell b with two texts
    "slide10.xml": slide(
        text(CW, 100000, CW, 400000, "Zehn"), pic(CW, 0, CW, CH), text(CW, CH - 400000, CW, 400000, "Seite 10")
    ),
}


def write_pptx(path: Path, slides: dict[str, str] = SLIDES, size: bool = True) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        sld_sz = f'<p:sldSz cx="{W}" cy="{H}"/>' if size else ""
        archive.writestr("ppt/presentation.xml", f"<p:presentation {NAMESPACES}>{sld_sz}</p:presentation>")
        for name, content in reversed(slides.items()):  # neither archive order nor text order is slide order
            archive.writestr(f"ppt/slides/{name}", content)
            archive.writestr(f"ppt/slides/_rels/{name}.rels", "<Relationships/>")  # not a slide
    return str(path)


@pytest.fixture
def pptx(tmp_path):
    return write_pptx(tmp_path / "karten.pptx")


# --- slug / cards_of_slide --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Der kleine Drache!", "der-kleine-drache"),
        ("  Über   Bär & Co. ", "über-bär-co"),
        ("", "karte"),
        ("!!! ???", "karte"),
        ("a" * 50, "a" * 40),
    ],
)
def test_slug(value, expected):
    assert slug(value) == expected


def test_slug_length_strips_trailing_separator():
    assert slug("abc def", length=4) == "abc"


def test_cards_of_slide_assigns_shapes_by_center():
    shapes: list[import_cards.Shape] = [
        ("pic", 0, 0, CW, CH, ""),
        ("text", CW - 100, 0, 1000, 1000, "Titel a"),  # starts in a, center in b
        ("text", 0, CH - 400, 1000, 1000, "Titel c"),  # starts in a, center in c
        ("pic", CW - 1000, CH - 1000, CW, CH, ""),  # center in d
        ("text", 2 * W, 2 * H, 10, 10, "draußen"),  # outside the slide: clamped to the last cell
        ("text", 10, 10, 10, 10, "auch a"),
    ]
    assert cards_of_slide(shapes, W, H, 2, 2) == [(0, 0, ["auch a"]), (1, 1, ["draußen"])]


def test_cards_of_slide_skips_cells_without_pictures():
    shapes: list[import_cards.Shape] = [("text", 0, 0, CW, CH, "Nur Text"), ("pic", CW, 0, CW, CH, "")]
    assert cards_of_slide(shapes, W, H, 2, 2) == [(1, 0, [])]
    assert cards_of_slide([], W, H, 2, 2) == []


def test_cards_of_slide_ignores_cell_letters():
    shapes: list[import_cards.Shape] = [
        ("pic", 0, 0, CW, CH, ""),
        ("text", 10, 10, 10, 10, "A"),  # the cell letter of the template
        ("text", 20, 20, 10, 10, "Titel"),
        ("pic", CW, 0, CW, CH, ""),
        ("text", CW + 10, 10, 10, 10, "B"),
    ]
    assert cards_of_slide(shapes, W, H, 2, 2) == [(0, 0, ["Titel"]), (1, 0, [])]


def test_cards_of_slide_keeps_text_order_and_grid_order():
    shapes: list[import_cards.Shape] = [
        ("pic", 0, CH, W, CH, ""),
        ("text", 0, H - 1000, 1000, 1000, "unten"),
        ("pic", 0, 0, W, CH, ""),
        ("text", 0, 0, 1000, 1000, "oben 1"),
        ("text", 0, 100, 1000, 1000, "oben 2"),
    ]
    assert cards_of_slide(shapes, W, H, 1, 2) == [(0, 0, ["oben 1", "oben 2"]), (0, 1, ["unten"])]


# --- read_presentation ------------------------------------------------------------------------------------------------


def test_read_presentation(pptx):
    width, height, slides = read_presentation(pptx)
    assert (width, height) == (W, H)
    assert slides == [
        [
            ("pic", 0, 0, CW, CH, ""),
            ("text", 0, CH - 500000, CW, 400000, "Der kleine Drache"),
            ("text", CW, 0, CW, CH, "Nur Text"),
            ("pic", CW, CH, CW, CH, ""),
        ],
        # group transform: child coordinates (offset 1000/2000, scale 1/2) -> slide coordinates
        [("pic", 0, CH, CW, CH, ""), ("text", 0, 2 * CH - 200000, CW, 200000, "Ritter")],
        # slide10.xml comes after slide2.xml
        [
            ("text", CW, 100000, CW, 400000, "Zehn"),
            ("pic", CW, 0, CW, CH, ""),
            ("text", CW, CH - 400000, CW, 400000, "Seite 10"),
        ],
    ]


def test_read_presentation_nested_groups(tmp_path):
    inner = group((0, 0, 100, 100), (0, 0, 10, 10), pic(5, 5, 5, 5))
    outer = group((1000, 2000, 400, 400), (0, 0, 200, 200), inner)
    _, _, slides = read_presentation(write_pptx(tmp_path / "groups.pptx", {"slide1.xml": slide(outer)}))
    # inner: (5, 5, 5, 5) -> (50, 50, 50, 50); outer: scale 2 and offset (1000, 2000)
    assert slides == [[("pic", 1100, 2100, 100, 100, "")]]


def test_read_presentation_without_slide_size(tmp_path):
    with pytest.raises(SystemExit, match="no slide size"):
        read_presentation(write_pptx(tmp_path / "broken.pptx", size=False))


def test_read_presentation_without_shape_tree(tmp_path):
    empty = f"<p:sld {NAMESPACES}><p:cSld/></p:sld>"
    assert read_presentation(write_pptx(tmp_path / "empty.pptx", {"slide1.xml": empty})) == (W, H, [[]])


# --- main -------------------------------------------------------------------------------------------------------------


def test_main_without_pdftoppm(monkeypatch, pptx, tmp_path):
    monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: None)
    with pytest.raises(SystemExit, match="pdftoppm not found"):
        main([pptx, str(tmp_path / "cards")])
    assert not (tmp_path / "cards").exists()


@pytest.mark.parametrize(("arguments", "message"), [
    (["--slides", "0"], "slide numbers"),  # would be the last slide
    (["--slides", "99"], "slide numbers"),
    (["--grid", "6x5"], "1 to 26 cells"),
    (["--prefix", "../b"], "prefix"),
])  # fmt: skip
def test_main_rejects_invalid_slides_and_grids(monkeypatch, pptx, tmp_path, arguments, message):
    monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: "/usr/bin/" + name)
    with pytest.raises(SystemExit, match=message):
        main([pptx, str(tmp_path / "cards"), *arguments])
    assert not (tmp_path / "cards").exists()


class FakeTools:
    """Replaces LibreOffice (convert_to_pdf) and pdftoppm (render_cell) - records the calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.converted: list[tuple[str, str]] = []
        self.rendered: list[tuple[Any, ...]] = []
        monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(import_cards, "convert_to_pdf", self.convert_to_pdf)
        monkeypatch.setattr(import_cards, "render_cell", self.render_cell)

    def convert_to_pdf(self, pptx: str, folder: str) -> str:
        self.converted.append((pptx, folder))
        return os.path.join(folder, "cards.pdf")

    def render_cell(self, pdf: str, slide: int, column: int, row: int, columns: int, rows: int, width_px: int,
                    height_px: int, dpi: int, target: str) -> None:  # fmt: skip
        self.rendered.append((os.path.basename(pdf), slide, column, row, columns, rows, width_px, height_px, dpi))
        Path(target).write_bytes(b"jpeg")


def test_main_imports_cards(monkeypatch, pptx, tmp_path, capsys):
    tools = FakeTools(monkeypatch)
    folder = tmp_path / "cards"
    folder.mkdir()
    (folder / "captions.json").write_text(json.dumps({"alt.jpg": "Alt", "01-d-karte.jpg": "wird ersetzt"}))

    main([pptx, str(folder), "--dpi", "100"])

    assert len(tools.converted) == 1
    assert tools.converted[0][0] == pptx
    assert os.path.dirname(tools.converted[0][1]) == str(folder)  # work folder inside the cards folder ...
    assert not os.path.exists(tools.converted[0][1])  # ... removed afterwards
    # 10 x 5 inch at 100 dpi
    assert tools.rendered == [
        ("cards.pdf", 1, 0, 0, 2, 2, 1000, 500, 100),
        ("cards.pdf", 1, 1, 1, 2, 2, 1000, 500, 100),
        ("cards.pdf", 2, 0, 1, 2, 2, 1000, 500, 100),
        ("cards.pdf", 3, 1, 0, 2, 2, 1000, 500, 100),
    ]
    captions = {
        "01-a-der-kleine-drache.jpg": "Der kleine Drache",
        "01-d-karte.jpg": "",
        "02-c-ritter.jpg": "Ritter",
        "03-b-zehn.jpg": "Zehn · Seite 10",
    }
    assert sorted(os.listdir(folder)) == sorted([*captions, "captions.json"])
    assert json.loads((folder / "captions.json").read_text(encoding="utf-8")) == {"alt.jpg": "Alt", **captions}
    out, err = capsys.readouterr()
    assert "03-b-zehn.jpg  Zehn · Seite 10" in out
    assert "4 card images" in err


def test_main_selected_slides_and_grid(monkeypatch, pptx, tmp_path):
    tools = FakeTools(monkeypatch)
    folder = tmp_path / "new" / "cards"  # created

    main([pptx, str(folder), "--grid", "1X1", "--slides", "3,1"])

    assert [call[1:6] for call in tools.rendered] == [(3, 0, 0, 1, 1), (1, 0, 0, 1, 1)]
    assert json.loads((folder / "captions.json").read_text(encoding="utf-8")) == {
        "03-a-zehn.jpg": "Zehn · Seite 10",
        "01-a-der-kleine-drache.jpg": "Der kleine Drache · Nur Text",
    }


def test_main_with_prefix(monkeypatch, pptx, tmp_path):
    FakeTools(monkeypatch)
    folder = tmp_path / "cards"
    main([pptx, str(folder), "--slides", "2", "--prefix", "b"])
    assert sorted(os.listdir(folder)) == ["b02-c-ritter.jpg", "captions.json"]
    assert json.loads((folder / "captions.json").read_text(encoding="utf-8")) == {"b02-c-ritter.jpg": "Ritter"}


def test_main_with_broken_captions_file(monkeypatch, pptx, tmp_path):
    FakeTools(monkeypatch)
    folder = tmp_path / "cards"
    folder.mkdir()
    (folder / "captions.json").write_text("{broken")
    main([pptx, str(folder), "--slides", "2"])
    assert json.loads((folder / "captions.json").read_text(encoding="utf-8")) == {"02-c-ritter.jpg": "Ritter"}


# --- convert_to_pdf / render_cell (external programs replaced) -------------------------------------------------------


def test_convert_to_pdf(monkeypatch, pptx, tmp_path):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).with_suffix(".pdf").write_bytes(b"%PDF")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: f"/opt/{name}")
    monkeypatch.setattr("tinyjukebox.tools.import_cards.subprocess.run", run)
    work = str(tmp_path / "work")
    os.mkdir(work)
    assert import_cards.convert_to_pdf(pptx, work) == os.path.join(work, "cards.pdf")
    copy = os.path.join(work, "cards.pptx")
    assert calls == [["/opt/soffice", "--headless", "--convert-to", "pdf", "--outdir", work, copy]]
    assert os.listdir(work) == ["cards.pdf"]  # the copy is removed


def test_convert_to_pdf_failures(monkeypatch, pptx, tmp_path):
    monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: None)
    with pytest.raises(SystemExit, match="LibreOffice"):
        import_cards.convert_to_pdf(pptx, str(tmp_path))
    monkeypatch.setattr("tinyjukebox.tools.import_cards.shutil.which", lambda name: f"/opt/{name}")
    monkeypatch.setattr("tinyjukebox.tools.import_cards.subprocess.run", lambda cmd, **kwargs: None)
    with pytest.raises(SystemExit, match=r"converting .* failed"):
        import_cards.convert_to_pdf(pptx, str(tmp_path))


def test_render_cell_command(monkeypatch):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr("tinyjukebox.tools.import_cards.subprocess.run", run)
    import_cards.render_cell("cards.pdf", 3, 1, 1, 2, 2, 1001, 501, 100, "/cards/03-d-x.jpg")
    assert calls == [
        ["pdftoppm", "-jpeg", "-jpegopt", "quality=85", "-r", "100", "-f", "3", "-l", "3", "-x", "500", "-y", "250",
         "-W", "500", "-H", "250", "-singlefile", "cards.pdf", "/cards/03-d-x"],
    ]  # fmt: skip
