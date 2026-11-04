# Concept: the documentation as a site

Status: concept, nothing implemented. Written on 2026-11-04 against version 0.46.0 (not yet released), after the
site of edakit (<https://tgd1975.github.io/edakit/>, edakit 1.1.17). Decisions D1 to D5 are open (section 9). The plan
is section 10.
Related today: the documents and their rules ([style guide](../../style-guide/README.md), section 8), the documents
test (`tests/unit/test_documents.py`), the pipeline ([library.md](../library.md), section 8), the release
([development.md](../development.md)).

## 1. What it is

The documentation of Tiny Jukebox as one web site: the same Markdown files as in the repository, built by a script
into a site with tabs by reader, search, a light and a dark theme in the colours of the style guide, built on every
push and published with every push to `master` – that is, with every release. The files stay where they are and stay
readable on GitHub; the site is a second view of them, not a second source.

What changes for a reader: one address instead of the repository's folders; a navigation that shows the pages of
one reader, not all 23 documents; a search over all of them, in English and German; the diagrams drawn.

What does not change: how the documents are written (style guide, section 8), where they lie, the rule that the user
documentation exists in both languages with the same structure.

## 2. What edakit does

The model, read from edakit's `docs/pages.py`, `.github/workflows/pages.yml` and the published site:

| Part | How edakit does it |
| --- | --- |
| generator | MkDocs 1.6.1 with Material 9.6.23, both pinned and run with `uvx` – no dependency of the project |
| assembly | a script (`docs/pages.py`, 635 lines) copies the Markdown into `build/pages/`, rewrites the links and writes `mkdocs.yml` as JSON |
| navigation | data in the script: a table of tab, file and short title; tabs by reader ("Start", "A circuit", "A board", "Extend", "Develop", "Handbook", "Reference" …); at most eight entries in a tab's sidebar |
| addresses | a page lands under its tab (`/start/…`, `/handbook/…`); the files in the repository stay where they are |
| links | to a page of the site: its address there; an image: copied onto the site (`files/<path>`); anything else: the file on GitHub |
| generated pages | indexes of concepts, skills and agents; the rule catalog (its own generator) |
| sources | concepts, prompts and the rules of a session behind their own tab, each page marked "A source" |
| look | Material with the brand's colours in a small stylesheet (`docs/brand/edakit.css`), logo and favicon from the brand folder, light and dark following the system |
| footer | "Built from edakit 1.1.17" and a link to open an issue |
| check | `mkdocs build --strict` on every push – a broken link or anchor fails the build |
| publishing | a separate workflow; the job that publishes runs only on `main`, with the only write permissions (`pages`, `id-token`) |
| moved pages | a redirect page at the old address, so links from outside hold |

## 3. What Tiny Jukebox has

23 Markdown documents with 131,000 words, from the quick guide (286 words) to `library.md` (35,800). Mermaid diagrams
in four of them, 18 photos and drawings in `docs/images/`, the logo in four SVGs, the user documentation in English and
German.

A trial build on 2026-11-04 (MkDocs 1.6.1, Material 9.6.23, all documents copied as they are, no navigation):

| Measure | Result |
| --- | --- |
| build time | 1.6 s; the site 12 MB, most of it the photos |
| warnings | 15, all of them links to files that are not documents: `docs/config.example.yaml`, `docs/config_schema.json`, `deploy/config/*.yaml`, `deploy/…/tinyjukebox.service`, `install.sh`, `update_box.sh`, `LICENSE.txt` – 12 files |
| anchors | 3 broken with MkDocs' own slugs: the German manual links to `#5-bluetooth-kopfhörer`, MkDocs drops the umlaut. With `pymdownx.slugs.slugify` (lower case, Unicode kept) the anchors are those of GitHub, and there are none broken |
| diagrams | drawn by Material from the ```` ```mermaid ```` fences, without a change to the files |

The documents are ready for it: numbered sections, relative links, `alt` texts – the style guide's rules hold on the
site as they are.

## 4. Readers and tabs

Tiny Jukebox has four readers: the person who uses a box (a parent, in German or in English), the person who sets up
boxes and the library, the person who builds the hardware, the developer. The tabs follow them:

| Tab | Pages (short titles) |
| --- | --- |
| Start | Tiny Jukebox (the README, as the site's start page) · Changelog · Open points and roadmap |
| Use | Quick guide · Manual · Kurzanleitung · Anleitung |
| Set up | Deployment · Configuration · Operations · Library |
| Hardware | The box · Circuit and printed parts (`hardware/README.md`) · Key data and data sheets (`docs/hardware/README.md`) |
| Develop | Architecture · Drivers · rfid-reader · REST API and web page · The app · Development · Style guide |
| Reference | Configuration keys (generated, section 6.3) · Example config · Config schema |
| Concepts | the documents of `docs/developer/concept/`, each marked as a plan, not as what the software does |

Every tab has at most seven entries. `library.md` stands under "Set up": its first reader is the person who puts the
library on the NAS; its sections on synchronization and the pipeline are reached from "Develop" through the links that
are already there.

The README is the start page. Its section 4 (the tables of documents) stays: on GitHub it is the navigation, on the
site it is a second one, and the test of section 7 keeps both in step.

Addresses follow the tabs, as in edakit: `/use/manual-de/`, `/develop/architecture/`. There are no old addresses yet,
so no redirects.

## 5. Look

Material, configured from the style guide – no new colours, no new rules:

- **Colours:** the header plum ink `#3a1f33` with white text (14.8 : 1), as the status bar of the app; links and
  accent berry `#8e2f66`. In the dark theme (Material's "slate") the background night `#16111a`, links and accent berry
  light `#d77aae` (6.5 : 1). Light and dark follow the system, with Material's switch in the header. The values go into
  one stylesheet (`docs/site/tinyjukebox.css`), and the style guide names it in its table of sources in the same
  commit.
- **Logo and favicon:** `logo-icon.svg` as favicon, `logo-mark.svg` in the header – its light sides and the berry front
  stand out on plum ink; no new drawing.
- **Type:** no web fonts (`font: false`). The style guide says so for the page of the box, and for the site it
  keeps Material from loading fonts from Google – a request to a third party from every reader.
- **Search:** Material's search with English and German (`lang: [en, de]`), so that "Kopfhörer" finds the German
  manual.
- **Footer:** "Built from Tiny Jukebox 0.46.0", the version from `pyproject.toml`, and where to report a mistake (see
  D1).

The style guide gets a section 3.5 "The documentation site" with the table of these values, as it has for the app and
the page.

## 6. Assembly

### 6.1 The script

`scripts/site/assemble.py` – plain Python, no dependency – writes `build/site-src/`: the documents under their site
paths, the images, the generated pages and `mkdocs.yml`. The navigation is a table in the script, as in edakit.

`scripts/site/build.sh` runs it and then `uvx --with mkdocs-material==9.6.23 --from mkdocs==1.6.1 mkdocs build
--strict`. Locally the same command, plus `mkdocs serve` for writing with a preview. MkDocs stays out of
`pyproject.toml` and `uv.lock`: the box and the library do not need it.

The name `scripts/pages/` is taken: there it means the web pages of the box and of the library. Here it is "the site".

### 6.2 Links

| Link to | On the site |
| --- | --- |
| a document of the navigation | its address on the site, with the anchor |
| an image | a copy on the site |
| one of the 12 files that are not documents (section 3) | a copy on the site, shown as text – or the file on GitHub (D1) |
| a folder (`hardware/`) | its README if it is a page, otherwise GitHub |

Anchors are GitHub's: `toc` with `pymdownx.slugs.slugify` and `case: lower`. A link that works on GitHub works on the
site, and the other way round.

### 6.3 Generated pages

One, at first: **Configuration keys**, from `docs/config_schema.json` – every key with its type, default and
description, in the order of the schema. `configuration.md` describes the keys in prose; the generated table is what
is looked up. Later candidates, each only if asked for: the LED table from `rgbled.py` and `messages.py` (today in the
style guide and the manuals by hand), the endpoints from the server's routes.

### 6.4 The German pages

The site's language is English; the German quick guide and manual are pages of their own under "Use", with
`lang: de` in their front matter written by the script, so that the browser hyphenates and reads them as German. A
switch between the languages is not needed: the two manuals have the same structure, and each links to the other at
the top (new, one line in each, enforced by the documents test). A plugin for a second language (`mkdocs-static-i18n`)
would build a German site with four German pages and 19 English ones – more machinery than readers.

## 7. Check and pipeline

- **Test** `tests/unit/test_site.py`, in the normal test run: every document of the repository is in the navigation or
  named in a list of those left out on purpose; no tab has more than eight entries; the script runs and every link of
  the assembled pages leads to a page, a file or an anchor of the site. It runs without MkDocs – the script is plain
  Python, so the test needs no network.
- **Build** with `--strict` on every push and pull request, in a workflow of its own (`.github/workflows/site.yml`),
  so that a broken link fails before the merge. The actions pinned to a commit, as in `ci.yml`.
- **Publish** only from `master`, in a second job with `pages: write` and `id-token: write`; the build job reads only.
  A push to `master` is a release (library.md, section 8), so the site shows the documents of the latest release.
- **Once, by hand:** Settings → Pages → Build and deployment → Source "GitHub Actions".

## 8. What is not taken from edakit

- the chat transcripts (```` ```chat ```` fences) – Tiny Jukebox has no sessions to show
- the tab of sources with skills, agents and evals – the concepts are the only sources, and they get a tab of their own
- word budgets and the newcomer's eval – the quick guides are already short (286 and 331 words) and the manuals are the
  person's manual; a measure can follow when the site has readers
- the redirects – nothing has moved yet; they come with the first page that moves

## 9. Decisions

**D1 – Who may read the site.** The repository is private. GitHub Pages from a private repository needs a paid plan
(GitHub Pro for a personal account), and the site is then public: anyone with the address reads it; only Enterprise
Cloud limits a site to members. The documents name no addresses but the example names `tinyjukebox.local` and
`kitchen.local`, and no secrets; they show the photos of the box and its history.

| Option | What it means |
| --- | --- |
| a) public site, all documents (recommended if the documents may be public) | as edakit; the 12 files of section 3 copied onto the site, because links to the private repository give a reader a 404; no link "open an issue" – a reader cannot reach the issues |
| b) public site, only "Use", "Hardware" and the start page | the user documentation and the hardware for anyone; the rest stays in the repository |
| c) no GitHub Pages: the library serves the site | the built site goes into the package of the library and is served under `/docs/` of its page – private as the library, offline, the version of the library's own release; more work in the package and the server |
| d) make the repository public | as a), with links to GitHub and the issues; a decision beyond the documentation |

**D2 – The tabs** as in section 4. Recommended as written.

**D3 – German pages** as pages of their own in an English site (section 6.4), not a second site. Recommended.

**D4 – Generated configuration keys** (section 6.3) in the first step, or later. Recommended: in the first step – the
schema is complete today, and the page is what an administrator looks up most.

**D5 – Where the site is checked.** A workflow of its own (recommended, as edakit: publishing needs other permissions
than the tests) or a job in `ci.yml`.

## 10. Plan

| Step | What | Done when |
| --- | --- | --- |
| S1 | `scripts/site/assemble.py` and `build.sh`, the navigation of section 4, the link rewriting of 6.2, the slugs | the site builds locally with `--strict` and no warning |
| S2 | the look of section 5: stylesheet, logo, no web fonts, search in two languages; style guide section 3.5 | light and dark checked in a browser; contrast of the new pairs measured as for the page |
| S3 | the generated configuration keys (if D4) and the links between the two manuals | the documents test holds the links |
| S4 | `tests/unit/test_site.py` | in the normal test run |
| S5 | `.github/workflows/site.yml`; Pages switched on (by the owner, D1) | the first push to `master` publishes; README section 4 links the site |
| S6 | `CHANGELOG.md`, `development.md` (how to build the site), roadmap | in the release that brings it |

S1 to S4 are one release; S5 needs D1 first. The size, measured on edakit: about 300 lines of script (without
edakit's chats and indexes), a stylesheet of 30 lines, a test of 100, a workflow of 50.
