"""tinyjukebox-import-cards - cut the pictures of cards out of a PowerPoint file (several cards per slide) into a
folder.

    tinyjukebox-import-cards cards.pptx OUTPUT-FOLDER --grid 2x2 --slides 1,3,5

Needs LibreOffice (pptx -> pdf) and pdftoppm (poppler-utils). Every grid cell that contains at least one picture
becomes a card image '<prefix><slide>-<cell>-<title>.jpg'; the texts on the card are stored as caption in
captions.json. Use --prefix to keep the cards of several files apart in one folder.
To make the images covers of albums, name them ``album-<id>.jpg`` and list them in captions.json
(``tinyjukebox-import-covers``).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from typing import Any
from xml.etree import ElementTree

__all__ = ["cards_of_slide", "main", "read_presentation", "slug"]

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
CAPTIONS_FILE = "captions.json"


def _slide_names(archive: zipfile.ZipFile) -> list[str]:
    """Slide xml files in presentation order."""
    names = [n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    return sorted(names, key=lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[1])))


def _xfrm(element: ElementTree.Element, properties: str) -> tuple[int, int, int, int, ElementTree.Element] | None:
    """(x, y, cx, cy, xfrm element) of a shape or None."""
    xfrm = element.find(f"p:{properties}/a:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    return int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)), int(ext.get("cy", 0)), xfrm


Transform = Callable[[int, int, int, int], tuple[int, int, int, int]]
Shape = tuple[str, int, int, int, int, str]


def _identity(x: int, y: int, cx: int, cy: int) -> tuple[int, int, int, int]:
    return x, y, cx, cy


def _shapes(element: ElementTree.Element, transform: Transform = _identity) -> list[Shape]:
    """(kind, x, y, cx, cy, text) of all pictures and text shapes in slide coordinates - groups are resolved."""
    result: list[Shape] = []
    for child in element:
        tag = child.tag.split("}")[1]
        if tag == "grpSp":
            position = _xfrm(child, "grpSpPr")
            if position is None:
                continue
            x, y, cx, cy, xfrm = position
            ch_off, ch_ext = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
            if ch_off is None or ch_ext is None:
                continue
            cox, coy = int(ch_off.get("x", 0)), int(ch_off.get("y", 0))
            ccx, ccy = max(1, int(ch_ext.get("cx", 1))), max(1, int(ch_ext.get("cy", 1)))

            def child_transform(
                x2: int,
                y2: int,
                cx2: int,
                cy2: int,
                x: int = x,
                y: int = y,
                cx: int = cx,
                cy: int = cy,
                cox: int = cox,
                coy: int = coy,
                ccx: int = ccx,
                ccy: int = ccy,
                outer: Transform = transform,
            ) -> tuple[int, int, int, int]:
                return outer(x + (x2 - cox) * cx // ccx, y + (y2 - coy) * cy // ccy, cx2 * cx // ccx, cy2 * cy // ccy)

            result += _shapes(child, child_transform)
            continue
        if tag not in ("pic", "sp"):
            continue
        position = _xfrm(child, "spPr")
        if position is None:
            continue
        x, y, cx, cy = transform(*position[:4])
        if tag == "pic":
            result.append(("pic", x, y, cx, cy, ""))
        else:
            paragraphs = ["".join(t.text or "" for t in p.findall(".//a:t", NS)) for p in child.findall(".//a:p", NS)]
            text = " ".join(p.strip() for p in paragraphs if p.strip())
            if text:
                result.append(("text", x, y, cx, cy, text))
    return result


def read_presentation(filename: str) -> tuple[int, int, list[list[Shape]]]:
    """Returns (slide width, slide height, [shapes of slide 1, shapes of slide 2, ...]) in EMU."""
    with zipfile.ZipFile(filename) as archive:
        presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))  # noqa: S314
        size = presentation.find("p:sldSz", NS)
        slides = []
        for name in _slide_names(archive):
            tree = ElementTree.fromstring(archive.read(name))  # noqa: S314
            tree_root = tree.find(".//p:spTree", NS)
            slides.append(_shapes(tree_root) if tree_root is not None else [])
    if size is None:
        raise SystemExit(f"'{filename}' has no slide size.")
    return int(size.get("cx", 0)), int(size.get("cy", 0)), slides


def cards_of_slide(
    shapes: list[Shape], width: int, height: int, columns: int, rows: int
) -> list[tuple[int, int, list[str]]]:
    """Grid cells with at least one picture: [(column, row, caption texts)] - a shape belongs to the cell of its
    center. Texts of a single character (the cell letters of the template) are no captions."""
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for kind, x, y, cx, cy, text in shapes:
        column = min(columns - 1, max(0, (x + cx // 2) * columns // width))
        row = min(rows - 1, max(0, (y + cy // 2) * rows // height))
        cell = cells.setdefault((column, row), {"pictures": 0, "texts": []})
        if kind == "pic":
            cell["pictures"] += 1
        elif len(text) > 1:
            cell["texts"].append(text)
    return [
        (c, r, cells[(c, r)]["texts"])
        for r in range(rows)
        for c in range(columns)
        if (c, r) in cells and cells[(c, r)]["pictures"] > 0
    ]


def slug(text: str, length: int = 40) -> str:
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-").lower()
    return text[:length].strip("-") or "karte"


def convert_to_pdf(pptx: str, folder: str) -> str:
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None:
        raise SystemExit("LibreOffice (soffice) not found.")
    # snap packages cannot read hidden folders or /tmp - work on a copy in the output folder
    copy = os.path.join(folder, "cards.pptx")
    shutil.copy(pptx, copy)
    subprocess.run(
        [office, "--headless", "--convert-to", "pdf", "--outdir", folder, copy],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.remove(copy)
    pdf = os.path.join(folder, "cards.pdf")
    if not os.path.exists(pdf):
        raise SystemExit(f"converting '{pptx}' to pdf failed.")
    return pdf


def render_cell(
    pdf: str,
    slide: int,
    column: int,
    row: int,
    columns: int,
    rows: int,
    width_px: int,
    height_px: int,
    dpi: int,
    target: str,
) -> None:
    cell_w, cell_h = width_px // columns, height_px // rows
    base = target[:-4]
    subprocess.run(
        [
            "pdftoppm",
            "-jpeg",
            "-jpegopt",
            "quality=85",
            "-r",
            str(dpi),
            "-f",
            str(slide),
            "-l",
            str(slide),
            "-x",
            str(column * cell_w),
            "-y",
            str(row * cell_h),
            "-W",
            str(cell_w),
            "-H",
            str(cell_h),
            "-singlefile",
            pdf,
            base,
        ],
        check=True,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tinyjukebox-import-cards", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("pptx", help="PowerPoint file with the card fronts")
    parser.add_argument("folder", help="folder for the images")
    parser.add_argument("--grid", default="2x2", help="cards per slide as COLUMNSxROWS (default: 2x2)")
    parser.add_argument("--slides", help="comma separated slide numbers (default: every slide with pictures)")
    parser.add_argument("--dpi", type=int, default=150, help="resolution of the card images (default: 150)")
    parser.add_argument("--prefix", default="", help="start of the image names, e.g. 'b' for a second file")
    args = parser.parse_args(argv)

    if shutil.which("pdftoppm") is None:
        raise SystemExit("pdftoppm not found - install poppler-utils.")
    columns, rows = (int(v) for v in args.grid.lower().split("x"))
    width, height, slides = read_presentation(args.pptx)
    selected = [int(s) for s in args.slides.split(",")] if args.slides else list(range(1, len(slides) + 1))
    if any(not 1 <= number <= len(slides) for number in selected):
        raise SystemExit(f"slide numbers must be between 1 and {len(slides)}.")
    if columns * rows > 26 or min(columns, rows) < 1:
        raise SystemExit("the grid must have 1 to 26 cells.")
    if not re.fullmatch(r"[\w-]*", args.prefix):
        raise SystemExit("the prefix may only contain letters, digits, '-' and '_'.")
    os.makedirs(args.folder, exist_ok=True)
    captions_file = os.path.join(args.folder, CAPTIONS_FILE)
    try:
        with open(captions_file, encoding="UTF-8") as f:
            captions = json.load(f)
    except (FileNotFoundError, ValueError):
        captions = {}

    width_px = round(width / 914400 * args.dpi)
    height_px = round(height / 914400 * args.dpi)
    with tempfile.TemporaryDirectory(dir=args.folder) as work:
        pdf = convert_to_pdf(args.pptx, work)
        count = 0
        for number in selected:
            for column, row, texts in cards_of_slide(slides[number - 1], width, height, columns, rows):
                cell = "abcdefghijklmnopqrstuvwxyz"[row * columns + column]
                name = f"{args.prefix}{number:02d}-{cell}-{slug(texts[0] if texts else 'karte')}.jpg"
                render_cell(
                    pdf,
                    number,
                    column,
                    row,
                    columns,
                    rows,
                    width_px,
                    height_px,
                    args.dpi,
                    os.path.join(args.folder, name),
                )
                captions[name] = " · ".join(texts)
                count += 1
                print(f"{name}  {captions[name]}")
    with open(captions_file, "w", encoding="UTF-8") as f:
        json.dump(captions, f, indent=2, sort_keys=True, ensure_ascii=False)
    print(f"{count} card images in '{args.folder}'.", file=sys.stderr)


if __name__ == "__main__":
    main()
