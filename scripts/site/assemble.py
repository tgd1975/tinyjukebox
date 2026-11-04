"""The documentation of Tiny Jukebox as one site: assembled into build/site-src and built by MkDocs into build/site
(docs/developer/development.md, section 10). Plain Python, no dependency of the project.

    python3 scripts/site/assemble.py            writes build/site-src: the pages, the images and mkdocs.yml
    scripts/site/build.sh                       assembles and builds the site with --strict into build/site
    scripts/site/build.sh serve                 the same as a preview on http://127.0.0.1:8000

The navigation is data (NAV): tabs by reader, each a list of pages. The documents stay where they are and stay
readable on GitHub; the site is a second view of them. Only the user manuals are cut: one file in the repository,
one page per entry of the menu on the site (MANUAL_PAGES). A link to a page of the site becomes its address there,
an image a copy on the site, a link to anything else the file on GitHub.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "https://github.com/tgd1975/tinyjukebox"
SITE = "https://tgd1975.github.io/tinyjukebox/"
BRANCH = "master"
OUT = ROOT / "build" / "site-src"
STYLE = "docs/site/tinyjukebox.css"
MEDIA = (".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp")
LINK = re.compile(r"(!?\[(?:[^\]\[]|\[[^\]]*\])*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
ATTR = re.compile(r'(<(?:a|img|source)\s[^>]*?\b(?:href|src|srcset)=")([^"]+)(")')
FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
FENCE_FORMAT = "!!python/name:pymdownx.superfences.fence_code_format"
SLUGIFY = "!!python/object/apply:pymdownx.slugs.slugify {kwds: {case: lower}}"

# The manual of each language as pages: (page, title in English, title in German, chapters). The chapters are the
# numbers of the `## N.` headings of docs/user/<language>/manual.md; the text before chapter 1 goes onto the first
# page. Every chapter is on exactly one page (tests/unit/test_site.py).
MANUAL_PAGES = [
    ("the-box", "The box", "Die Box", [1, 2, 3, 4, 5]),
    ("web-page", "Web page and users", "Webseite und Benutzer", [6, 10, 11]),
    ("mp3-albums", "MP3 albums", "MP3-Alben", [7]),
    ("radio", "Radio", "Radio", [8]),
    ("spotify", "Spotify", "Spotify", [9]),
    ("help", "If something does not work", "Wenn etwas nicht funktioniert", [12]),
    ("library", "Library", "Bibliothek", [13]),
]
LANGUAGES = {"en": ("use", "Use", "Quick guide", "Deutsch"), "de": ("bedienen", "Bedienen", "Kurzanleitung", "English")}

# The tabs: (tab, [(repository file, site path, short title)]). A tab's first page is its index where the site path
# is <tab>/index.md. "manual:<language>" stands for the pages of MANUAL_PAGES, "generated:" for a page this script
# writes, a URL for a link out of the site.
NAV = [
    (
        "Start",
        [
            ("README.md", "index.md", "Tiny Jukebox"),
            ("CHANGELOG.md", "start/changelog.md", "Changelog"),
            ("docs/developer/roadmap.md", "start/roadmap.md", "Open points and roadmap"),
        ],
    ),
    (
        "Use",
        [
            ("docs/user/en/quick-guide.md", "use/index.md", "Quick guide"),
            ("manual:en", "", ""),
        ],
    ),
    (
        "Bedienen",
        [
            ("docs/user/de/quick-guide.md", "bedienen/index.md", "Kurzanleitung"),
            ("manual:de", "", ""),
        ],
    ),
    (
        "Set up",
        [
            ("docs/developer/deployment.md", "setup/deployment.md", "Deployment"),
            ("docs/developer/configuration.md", "setup/configuration.md", "Configuration"),
            ("docs/developer/operations.md", "setup/operations.md", "Operations"),
            ("docs/developer/library.md", "setup/library.md", "Library and synchronization"),
            ("deploy/README.md", "setup/deploy-folder.md", "The folder deploy/"),
        ],
    ),
    (
        "Hardware",
        [
            ("hardware/README.md", "hardware/index.md", "Circuit and printed parts"),
            ("docs/developer/box.md", "hardware/box.md", "The box"),
            ("docs/hardware/README.md", "hardware/data-sheets.md", "Key data and data sheets"),
        ],
    ),
    (
        "Develop",
        [
            ("docs/developer/architecture.md", "develop/architecture.md", "Architecture"),
            ("docs/developer/drivers.md", "develop/drivers.md", "Drivers"),
            ("docs/developer/rfid-reader.md", "develop/rfid-reader.md", "rfid-reader"),
            ("docs/developer/rest-api.md", "develop/rest-api.md", "REST API and web page"),
            ("docs/developer/app.md", "develop/app.md", "The app"),
            ("docs/developer/development.md", "develop/development.md", "Development"),
            ("docs/style-guide/README.md", "develop/style-guide.md", "Style guide"),
        ],
    ),
    (
        "Reference",
        [
            ("generated:config-keys", "reference/index.md", "Configuration keys"),
            (f"{REPO}/blob/{BRANCH}/docs/config.example.yaml", "", "Example config"),
            (f"{REPO}/blob/{BRANCH}/docs/config_schema.json", "", "Config schema"),
        ],
    ),
    (
        "Concepts",
        [
            ("generated:concepts", "concepts/index.md", "Concepts"),
            ("concepts:", "", ""),  # every file of docs/developer/concept/, by name
        ],
    ),
]
SIDEBAR = 8  # pages a tab shows at most
CONCEPTS = "docs/developer/concept"
LEFT_OUT = {"TODO.md": "the list of open work, German, for the owner"}  # Markdown files that are not on the site
PLAN = (
    '!!! note "A plan"\n    This page is a concept: how a part of Tiny Jukebox could work, written before it is '
    "built. Its status line says what of it is decided and built; the other tabs say what the software does.\n"
)


def slug(text):
    """The anchor of a heading as GitHub and pymdownx.slugs.slugify (case lower) make it: the text without markup,
    lower case, letters (umlauts too), digits, spaces, hyphens and underscores kept, spaces turned into hyphens."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = unicodedata.normalize("NFC", text.replace("`", "").replace("*", "")).lower()
    text = "".join(c for c in text if c.isalnum() or c in " -_")
    return text.replace(" ", "-")


def headings(text):
    """[(level, slug)] of the headings of a Markdown text, outside code blocks."""
    return [(len(m.group(1)), slug(m.group(2))) for m in HEADING.finditer(FENCE.sub("", text))]


def version():
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def chapters(text):
    """(text before chapter 1, {number: text of the chapter}) of a manual, cut at its `## N.` headings."""
    parts = re.split(r"^(?=## \d+\. )", text, flags=re.M)
    head, out = parts[0], {}
    for part in parts[1:]:
        out[int(re.match(r"## (\d+)\.", part).group(1))] = part.rstrip().removesuffix("---").rstrip() + "\n"
    return head, out


def manual_intro(head):
    """The text before chapter 1 without its title and its list of contents: the menu is the contents."""
    head = re.sub(r"^# .+\n", "", head, count=1)
    head = re.sub(r"^(Contents|Inhalt):\n(?:\n?\d+\. .*\n)+", "", head, flags=re.M)
    return head.strip().removesuffix("---").strip() + "\n\n"


def tab_dir(language):
    return LANGUAGES[language][0]


def pages():
    """[(repository file or "", site path, title, tab, text)] of every Markdown page of the site, in the order of the
    navigation, then the concepts; text is the page before its links are rewritten."""
    out = []
    for tab, entries in NAV:
        for src, site, title in entries:
            if src.startswith("manual:"):
                language = src.split(":")[1]
                path = f"docs/user/{language}/manual.md"
                head, chap = chapters((ROOT / path).read_text(encoding="utf-8"))
                for n, (name, en, de, numbers) in enumerate(MANUAL_PAGES):
                    name_of_page = en if language == "en" else de
                    text = (manual_intro(head) if n == 0 else "") + "\n---\n\n".join(chap[c] for c in numbers)
                    # one chapter: its heading is the page's title; several: the page's title above them
                    text = (
                        re.sub(r"^## ", "# ", text, count=1, flags=re.M)
                        if len(numbers) == 1
                        else f"# {name_of_page}\n\n{text}"
                    )
                    out.append((path, f"{tab_dir(language)}/{name}.md", name_of_page, tab, text))
            elif src.startswith("generated:"):
                out.append(("", site, title, tab, GENERATED[src](site)))
            elif src == "concepts:":
                for p in sorted((ROOT / CONCEPTS).glob("*.md")):
                    text = p.read_text(encoding="utf-8")
                    out.append((p.relative_to(ROOT).as_posix(), concept_site(p), concept_title(text), tab, text))
            elif src.startswith("https://"):
                continue
            else:
                out.append((src, site, title, tab, (ROOT / src).read_text(encoding="utf-8")))
    return out


def concept_site(path):
    return f"concepts/{path.stem.removesuffix('-concept')}.md"


def concept_title(text):
    """The title of a concept without "Concept: ", its first letter upper case - short enough for the menu."""
    title = re.search(r"^# (?:Concept: )?(.+)$", text, re.M).group(1)
    return title[0].upper() + title[1:]


def concept_status(text):
    """The first sentence of a concept's status line: "concept, nothing implemented"."""
    m = re.search(r"^Status:\s*(.+?)(?:\.\s|\.?$)", text, re.M)
    return m.group(1).strip() if m else ""


def concepts_page(site):
    rows = []
    for p in sorted((ROOT / CONCEPTS).glob("*.md")):
        text = p.read_text(encoding="utf-8")
        title = concept_title(text)
        rows.append(
            f"| [{title}](developer/concept/{p.name}) | {concept_status(text)} |"
        )  # from docs/, as rewrite reads it
    return "\n".join(
        [
            "# Concepts",
            "",
            "A concept says how a part of Tiny Jukebox could work, before it is built: what "
            "it is, the decisions it needs and a plan. The roadmap lists them with the other open points.",
            "",
            "| Concept | Status |",
            "| --- | --- |",
            *rows,
            "",
        ]
    )


def schema_rows(node, path, required, rows):
    """The rows of the table of keys: key, type, required, description - depth first, in the order of the schema."""
    for key, value in node.get("properties", {}).items():
        name = f"{path}.{key}" if path else key
        kind = value.get("type", "")
        if "enum" in value:
            kind = " / ".join(f"`{v}`" for v in value["enum"])
        elif kind == "array" and "items" in value:
            kind = f"list of {value['items'].get('type', 'objects')}"
        text = " ".join(value.get("description", "").split()).replace("|", "\\|")
        rows.append(f"| `{name}` | {kind} | {'yes' if key in required else ''} | {text} |")
        schema_rows(value, name, set(value.get("required", [])), rows)


def config_keys_page(site):
    schema = json.loads((ROOT / "docs/config_schema.json").read_text(encoding="utf-8"))
    lines = [
        "# Configuration keys",
        "",
        "Every key of the config file of a box, generated from "
        "[`docs/config_schema.json`](../../docs/config_schema.json), the schema the box checks its config "
        "against when it starts. The keys are explained with their context in "
        "[Configuration](../../docs/developer/configuration.md); an example is "
        "[`docs/config.example.yaml`](../../docs/config.example.yaml).",
        "",
    ]
    head = ["| Key | Type | Required | Description |", "| --- | --- | --- | --- |"]
    for section, node in schema["properties"].items():
        if node.get("type") == "array":
            continue
        rows = []
        schema_rows(node, section, set(node.get("required", [])), rows)
        lines += [f"## {section}", "", " ".join(node.get("description", "").split()) or "", "", *head, *rows, ""]
    drivers = schema["properties"]["drivers"]
    lines += [
        "## drivers",
        "",
        " ".join(drivers.get("description", "").split()),
        "",
        "A list; every entry has a `type`, and the type decides the other keys.",
        "",
    ]
    for case in drivers["items"].get("allOf", []):
        then = case.get("then", {})
        kind = case.get("if", {}).get("properties", {}).get("type", {}).get("const", "?")
        rows = []
        schema_rows(then, "", set(then.get("required", [])), rows)
        lines += [f"### type `{kind}`", "", *head, *rows, ""]
    # the links were written from the repository's root; rewrite() reads them from the page's file, docs/
    return "\n".join(lines).replace("](../../", "](../")


GENERATED = {"generated:concepts": concepts_page, "generated:config-keys": config_keys_page}


def url_of(md):
    """The directory URL of a site path: "use/radio.md" → "use/radio/", "use/index.md" → "use/", "index.md" → ""."""
    stem = md.removesuffix(".md")
    return "" if stem == "index" else (stem.removesuffix("index") if stem.endswith("/index") else stem + "/")


def relpath(target, start):
    """target relative to the directory of the site file start (both site paths, "/" for a directory)."""
    rel = posixpath.relpath(target or ".", posixpath.dirname(start) or ".")
    return rel + ("/" if target.endswith("/") and rel != "." else "") if rel != "." else "./"


class Site:
    def __init__(self, entries):
        self.entries = entries
        self.whole = {src: site for src, site, _, _, _ in entries if src and not src.endswith("/manual.md")}
        self.anchors = {}  # (manual file, anchor) → site path
        self.first = {}  # manual file → its first page
        for src, site, _, _, text in entries:
            if src.endswith("/manual.md"):
                self.first.setdefault(src, site)
                for _, s in headings(text):
                    self.anchors[(src, s)] = site
        self.media = set()

    def page_of(self, rel, anchor):
        """The site path of the page that has rel (a repository file) at anchor, or None."""
        if rel in self.whole:
            return self.whole[rel]
        if rel in self.first:
            return self.anchors.get((rel, anchor), self.first[rel])
        return None

    def rewrite(self, text, src, site):
        """The page's links as the site needs them (module docstring); the links inside code blocks stay."""
        here = posixpath.dirname(src) if src else "docs"
        address = url_of(site) + "index.html"  # a file in the page's directory URL, for links in HTML

        def where(target, image, html):
            if re.match(r"[a-z][a-z0-9+.-]*:|//", target):
                return None
            path, _, anchor = target.partition("#")
            if not path:  # an anchor of this page - or of another page of a manual
                page = self.page_of(src, anchor) if src in self.first else None
                return None if page in (None, site) else self.link(page, anchor, site, html, address)
            rel = posixpath.normpath(posixpath.join(here, path))
            page = self.page_of(rel, anchor)
            if page is None and (ROOT / rel / "README.md").is_file():  # a folder whose README is a page
                page = self.page_of(posixpath.join(rel, "README.md"), anchor)
            if page:
                return self.link(page, anchor, site, html, address)
            if (ROOT / rel).is_file() and rel.lower().endswith(MEDIA):
                self.media.add(rel)
                return relpath("files/" + rel, address if html else site)
            kind = "tree" if (ROOT / rel).is_dir() else "blob"
            return f"{REPO}/{kind}/{BRANCH}/{rel}" + (f"#{anchor}" if anchor else "")

        def markdown(m):
            new = where(m.group(2), m.group(1).startswith("!"), False)
            return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}"

        def tag(m):
            new = where(m.group(2), not m.group(1).startswith("<a"), True)
            return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}"

        out, last = [], 0
        for m in FENCE.finditer(text):
            out += [ATTR.sub(tag, LINK.sub(markdown, text[last : m.start()])), m.group(0)]
            last = m.end()
        return "".join([*out, ATTR.sub(tag, LINK.sub(markdown, text[last:]))])

    @staticmethod
    def link(page, anchor, site, html, address):
        """A link to a page of the site: to its Markdown file from a Markdown link (MkDocs makes the URL), to its URL
        from an HTML tag (MkDocs leaves HTML as it is)."""
        target = relpath(url_of(page), address) if html else relpath(page, site)
        return target + (f"#{anchor}" if anchor else "")


def other_language(site):
    """The same page in the other language, for the line at the top of a page of "Use" and "Bedienen"."""
    for language, (folder, _, _, name) in LANGUAGES.items():
        if site.startswith(folder + "/"):
            other = next(f for lang, (f, _, _, _) in LANGUAGES.items() if lang != language)
            return name, other + site[len(folder) :], language
    return None


def page_text(entry, site_obj):
    src, site, _, _, text = entry
    text = site_obj.rewrite(text, src, site)
    switch = other_language(site)
    if switch:
        name, other, language = switch
        line = f'[{name}]({relpath(other, site)}){{ .tj-language hreflang="{"de" if language == "en" else "en"}" }}\n\n'
        if re.match(r"# ", text):
            text = re.sub(r"^# .+\n", lambda m: m.group(0) + "\n" + line, text, count=1)
        else:
            text = line + text
        if language == "de":
            text = f'<div lang="de" markdown>\n\n{text}\n\n</div>\n'
    if src == "README.md":  # its logo is its title: an h1 for search and screen readers, not shown
        text = '<h1 class="tj-hidden">Tiny Jukebox</h1>\n\n' + text
    if src.startswith(CONCEPTS + "/"):
        text = re.sub(r"^# .+\n", lambda m: m.group(0) + "\n" + PLAN, text, count=1)
    return text


def nav(entries):
    out = []
    for tab, items in NAV:
        pages_of_tab = []
        for src, site, title in items:
            if src.startswith("manual:"):
                language = src.split(":")[1]
                pages_of_tab += [
                    {(en if language == "en" else de): f"{tab_dir(language)}/{name}.md"}
                    for name, en, de, _ in MANUAL_PAGES
                ]
            elif src.startswith("https://"):
                pages_of_tab.append({title: src})
            elif src == "concepts:":
                pages_of_tab += [{title: site} for s, site, title, _, _ in entries if s.startswith(CONCEPTS + "/")]
            else:
                pages_of_tab.append({title: site})
        out.append({tab: pages_of_tab})
    return out


def config(entries):
    css = (ROOT / STYLE).read_bytes()
    return {
        "site_name": "Tiny Jukebox",
        "site_description": "The music box for children that RFID tags control – the documentation",
        "site_url": SITE,
        "repo_url": REPO,
        "repo_name": "tgd1975/tinyjukebox",
        "edit_uri": "",
        "copyright": (
            f"Built from Tiny Jukebox {version()} · Something unclear or wrong? "
            f'<a href="{REPO}/issues/new">Open an issue</a>.'
        ),
        "docs_dir": "docs",
        "site_dir": "../site",
        "use_directory_urls": True,
        "theme": {
            "name": "material",
            "language": "en",
            "font": False,  # no fonts from Google: the style guide uses the fonts of the system
            "logo": "files/docs/images/logo-mark.svg",
            "favicon": "files/docs/images/logo-icon.svg",
            "features": [
                "navigation.tabs",
                "navigation.indexes",
                "navigation.top",
                "search.highlight",
                "content.code.copy",
                "toc.follow",
            ],
            "palette": [
                {
                    "media": "(prefers-color-scheme: light)",
                    "scheme": "default",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/brightness-7", "name": "Dark theme"},
                },
                {
                    "media": "(prefers-color-scheme: dark)",
                    "scheme": "slate",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/brightness-4", "name": "Light theme"},
                },
            ],
        },
        "plugins": [{"search": {"lang": ["en", "de"]}}],
        # the file's hash in its URL: a browser that cached the old styles fetches the new ones with the new pages
        "extra_css": [f"files/{STYLE}?v={hashlib.sha256(css).hexdigest()[:8]}"],
        "markdown_extensions": [
            "tables",
            "admonition",
            "attr_list",
            "md_in_html",
            "pymdownx.details",
            {"toc": {"permalink": True, "slugify": "SLUGIFY"}},
            {
                "pymdownx.superfences": {
                    "custom_fences": [{"name": "mermaid", "class": "mermaid", "format": "FENCE_FORMAT"}]
                }
            },
            {"pymdownx.highlight": {"anchor_linenums": False}},
            "pymdownx.inlinehilite",
        ],
        "validation": {
            "nav": {"omitted_files": "warn", "not_found": "warn", "absolute_links": "info"},
            "links": {"not_found": "warn", "anchors": "warn", "absolute_links": "info", "unrecognized_links": "warn"},
        },
        "nav": nav(entries),
    }


def assemble(out=OUT):
    """Write the site's sources into out: docs/ with every page and image, mkdocs.yml beside it."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    docs = out / "docs"
    entries = pages()
    site = Site(entries)
    for entry in entries:
        f = docs / entry[1]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page_text(entry, site), encoding="utf-8")
    for rel in sorted(site.media | {STYLE, "docs/images/logo-mark.svg", "docs/images/logo-icon.svg"}):
        (docs / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, docs / "files" / rel)
    # JSON is YAML: MkDocs reads it as it is; the two Python tags go in unquoted
    text = json.dumps(config(entries), indent=1, ensure_ascii=False)
    text = text.replace('"SLUGIFY"', SLUGIFY).replace('"FENCE_FORMAT"', FENCE_FORMAT)
    (out / "mkdocs.yml").write_text(text, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"assembled: {assemble(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)}")
