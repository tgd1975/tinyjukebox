"""Looks at the pages of library and box in a real browser (started by check.sh, which says what it needs).

``connect``: both pages with every tab, the version card - with a release that waits, and the banner of a box that
asks - and the LED with given data, and the address of the library entered on the page of the box. ``connected``,
after the box was restarted: the box in the library, a tag placed in the browser, the albums with their playing
time, a station with a cover, the statistics, an album with a broken file and the sorting of the albums with given
data, a token, a box renamed, the box unlinked from the library on its page. Prints what it saw and ends with an
error if something failed.
"""

import base64
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, sync_playwright

from tinyjukebox import __version__
from tinyjukebox.sync import handshake
from tinyjukebox.sync.client import Client

LIBRARY, BOX = "https://localhost:18866/", "https://localhost:18443/"
TEXT_FILES = Path(__file__).parents[2] / "src/tinyjukebox/web/web/texts"
BOOTSTRAP = "bootstrap-password-of-the-simulation"  # deploy/config/library-sim.yaml
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN44OAAAANkAWE06YPDAAAAAElFTkSuQmCC"  # one red pixel
SLOTS = {
    "available": True, "version": "0.9.1", "active": "b", "mode": "automatic", "newest": "0.9.1", "fit": "ok",
    "problem": "", "versions": [], "discard": [], "history": [], "library": "",
    "script": 2, "origin": "releases", "releases": "https://api.github.com/repos/owner/box", "wait": 3,
    "offer": {"version": "0.9.2", "published": "2026-10-09T08:00:00Z", "size": 14000000, "notes": "- a fix",
              "due": "2026-10-12T08:00:00Z"},
    "slots": {"a": {"version": "0.9.0", "state": "confirmed", "since": "-", "starts": 0},
              "b": {"version": "0.9.1", "state": "probation", "since": "2026-10-06T10:00:00Z", "starts": 3}},
}  # fmt: skip
# every text of a page, shown or hidden, one per line: what its elements contain and what their attributes say
TEXTS = """(() => {
  const lines = [];
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.replace(/\\s+/g, " ").trim();
      if (text) lines.push(text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || ["SCRIPT", "STYLE"].includes(node.tagName)) return;
    for (const name of ["title", "aria-label", "placeholder", "data-confirm"]) {
      if (node.getAttribute(name)) lines.push(`[${name}] ${node.getAttribute(name)}`);
    }
    node.childNodes.forEach(walk);
  };
  walk(document.body);
  return lines.join("\\n") + "\\n";
})()"""
problems: list[str] = []
shots = ""


def watch(page: Page, name: str) -> None:
    def response(answer) -> None:
        # not logged in yet; an album without a cover; the tag that refused() creates twice
        expected = answer.url.endswith(("/api/session", "/cover")) or (
            answer.status == 409 and "/api/tags" in answer.url
        )
        if answer.status >= 400 and not expected:
            problems.append(f"{name}: {answer.status} {answer.request.method} {answer.url}")

    page.on("pageerror", lambda error: problems.append(f"{name}: error in the page: {error}"))
    page.on("response", response)


def login(page: Page, url: str) -> None:
    """Logs in, in German: what this check looks for are German texts. A browser that chose nothing gets English."""
    page.goto(url)
    page.wait_for_selector("#login-view:not([hidden])")
    expect(page.inner_text("#login-form button[type=submit]") == "Log in", "the page starts in English")
    page.select_option("#login-form select.language", "de")
    page.wait_for_function("document.documentElement.lang === 'de'")
    page.wait_for_selector("#login-view:not([hidden])")
    place = page.inner_text("#login-place")
    hued = page.evaluate("document.documentElement.classList.contains('box-hued')")
    if url == LIBRARY:
        expect(place == "Bibliothek" and not hued, f"the login page says it is the library, in its colors: {place}")
    else:
        expect(place.startswith("Box") and hued, f"the login page says it is a box, in colors of its own: {place}")
    if shots:
        page.screenshot(path=f"{shots}/login-{'library' if url == LIBRARY else 'box'}.png")
    page.fill("#login-form input[name=username]", "admin")
    page.fill("#login-form input[name=password]", "admin-password")
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#app-view:not([hidden])")


def tab(page: Page, name: str, wait: int = 800) -> str:
    page.click(f'.tabs button[data-tab="{name}"]')
    page.wait_for_timeout(wait)
    return " ".join(page.inner_text(f"#tab-{name}").split())


def shot(page: Page, path: str) -> None:
    """A screenshot of the whole page - of what the window shows, if Chromium does not finish the whole one (seen
    with pages that are no longer than the window)."""
    try:
        page.screenshot(path=path, full_page=True, timeout=4000)
    except PlaywrightError:
        page.screenshot(path=path)


def expect(condition: object, what: str) -> None:
    print(("ok      " if condition else "FAILED  ") + what)
    if not condition:
        problems.append(what)


def every_tab(page: Page, name: str) -> list[str]:
    names = [b.get_attribute("data-tab") or "" for b in page.query_selector_all(".tabs button") if b.is_visible()]
    for tab_name in names:
        text = tab(page, tab_name)
        expect(page.is_visible(f"#tab-{tab_name}") and text, f"{name}: tab {tab_name} shows something")
        if shots:
            shot(page, f"{shots}/{name}-{tab_name}.png")
            with open(f"{shots}/{name}-{tab_name}.txt", "w", encoding="utf-8") as file:
                file.write(page.evaluate(TEXTS))
    return names


def english(page: Page, name: str, title: str) -> None:
    """The page in English, chosen in the tab "System": every tab, and nowhere a key where a text belongs."""
    tab(page, "system")
    page.select_option("#tab-system select.language", "en")
    page.wait_for_function("document.documentElement.lang === 'en'")
    page.wait_for_selector("#app-view:not([hidden])")
    expect(page.evaluate("location.hash") == "#system", f"{name}: the page stays on its tab when the language changes")
    expect(page.title() == title and page.inner_text("#logout") == "Log out", f"{name}: the page is English")
    every_tab(page, f"{name}-en")
    with open(TEXT_FILES / "en.json", encoding="utf-8") as file:
        keys = set(json.load(file))
    shown = [re.sub(r"^\[[\w-]+\] ", "", line) for line in page.evaluate(TEXTS).splitlines()]
    expect(
        not keys.intersection(shown), f"{name}: no key is shown instead of a text: {sorted(keys.intersection(shown))}"
    )
    page.select_option(".topbar select.language", "de")
    page.wait_for_function("document.documentElement.lang === 'de'")
    expect(page.inner_text("#logout") == "Abmelden", f"{name}: ... and German again, chosen in the top bar")


def refused(box: Page) -> None:
    """What the box refuses, the page says in its language: the answer names a code, the page has the text."""
    tab(box, "tag-management")
    for _ in range(2):
        box.fill("#tag-new input[name=uid]", "04:AA:BB")
        box.click("#tag-new button[type=submit]")
        box.wait_for_timeout(600)
    expect("Den Tag 04:AA:BB gibt es schon." in box.inner_text("#toast"), "a refused request is said in German")


def version_card(box: Page) -> None:
    """The section "Version" and the banner, with a box with slots given to the page as data: the PC has none."""
    box.route("**/api/box/system", lambda route: route.fulfill(json=SLOTS))
    sent = []
    box.route("**/api/box/system/*", lambda route: (sent.append(route.request.url.rsplit("/", 1)[1]),
                                                     route.fulfill(json={"status": 0, "output": "done"})))  # fmt: skip
    tab(box, "system", 1200)
    expect("0.9.1 (Slot B, wird ausprobiert)" in box.inner_text("#ver-running"), "the version card shows the slots")
    expect("Releases von https://api.github.com/repos/owner/box" in box.inner_text("#ver-source"),
           "a box without a library names the releases as the source of its versions")  # fmt: skip
    said = " ".join(box.inner_text("#ver-offer").split())
    expect("Version 0.9.2 vom" in said and "von selbst installiert (3 Tage Wartezeit)" in said,
           f"a release that waits for its day is shown: {said}")  # fmt: skip
    expect(box.inner_text("#ver-offer-install") == "Jetzt installieren", "... with a button to install it now")
    expect(box.evaluate("[...document.querySelectorAll('#ver-mode-select option')].map((o) => o.value).join()")
           == "off,manual,automatic", "the three update modes can be chosen")  # fmt: skip
    if shots:
        shot(box, f"{shots}/box-system-release.png")
    box.select_option("#ver-mode-select", "manual")
    box.wait_for_timeout(500)
    expect(sent == ["mode"] and box.inner_text("#ver-output").strip() == "done", "a command of the version card")
    box.click("#ver-offer-install")
    box.wait_for_timeout(500)
    expect(sent == ["mode", "update"], f"the button installs the release: {sent}")
    expect(not box.is_visible("#version-banner"), "a box that installs by itself shows no banner")
    # a box that asks: the banner is on every tab until an admin answered
    box.unroute("**/api/box/system")
    box.route("**/api/box/system", lambda route: route.fulfill(json={**SLOTS, "mode": "manual"}))
    tab(box, "system", 1200)
    expect(box.evaluate("document.getElementById('ver-mode-select').selectedOptions[0].textContent") == "nachfragen",
           "the mode of a box without a library that asks is called so")  # fmt: skip
    expect(box.is_visible("#ver-offer-skip") and box.inner_text("#ver-offer-install") == "Installieren",
           "the section Version asks: install or skip")  # fmt: skip
    tab(box, "now")
    banner = " ".join(box.inner_text("#version-banner").split()) if box.is_visible("#version-banner") else ""
    expect("Version 0.9.2 ist verfügbar." in banner and "Installieren" in banner and "Überspringen" in banner,
           f"the banner asks on another tab too: {banner}")  # fmt: skip
    expect(box.evaluate("document.getElementById('version-banner').classList.contains('admin-only')"),
           "... and only an admin sees it")  # fmt: skip
    if shots:
        shot(box, f"{shots}/box-now-banner.png")
    box.click("#version-banner-notes")
    box.wait_for_timeout(500)
    expect(box.evaluate("location.hash") == "#system" and "a fix" in box.inner_text("#ver-offer-notes"),
           "what is new leads to the section Version")  # fmt: skip
    tab(box, "now")
    box.click("#version-banner-skip")
    box.wait_for_timeout(500)
    expect(sent == ["mode", "update", "skip"], f"skipping is sent to the box: {sent}")
    tab(box, "system")
    box.unroute("**/api/box/system")
    box.unroute("**/api/box/system/*")


def connect(library: Page, box: Page) -> None:
    expect(
        library.title() == "Tiny Jukebox Bibliothek" and box.title() == "Tiny Jukebox",
        "the two pages have their titles",
    )
    tabs = every_tab(library, "library")
    expect("boxes" in tabs and "statistics" in tabs and "now" not in tabs, f"the tabs of the library: {tabs}")
    tabs = every_tab(box, "box")
    expect("now" in tabs and "boxes" not in tabs, f"the tabs of the box: {tabs}")
    expect(tabs.index("statistics") == tabs.index("upload") - 1, f"... with the statistics before the upload: {tabs}")
    expect(library.is_visible("#app-view"), "the login to the box did not end the login to the library")

    version_card(box)
    colors = []
    # right after a status event: the next one, which draws the LED of the box again, comes 5 s later at the latest
    for _ in range(100):
        if box.evaluate("performance.now() - lastStatusTime") < 300:
            break
        box.wait_for_timeout(100)
    box.evaluate("renderLed({colors: ['GREEN', 'AQUA', 'BLUE', 'AQUA'], delay: 0.2})")
    for _ in range(4):
        colors.append(box.evaluate("getComputedStyle(document.getElementById('led')).getPropertyValue('--led-a')"))
        box.wait_for_timeout(200)
    expect(len(set(colors)) == 3, f"the LED shows a sequence of three colors: {colors}")

    box.fill("#lib-form input[name=url]", LIBRARY.rstrip("/"))
    box.fill("#lib-form input[name=bootstrap]", BOOTSTRAP)
    box.click("#lib-form button[type=submit]")
    box.wait_for_timeout(1200)
    expect("gespeichert" in box.inner_text("#toast"), "the address of the library is saved on the page of the box")
    expect("nächsten Start" in box.inner_text("#lib-state"), "... and takes effect with the next start")


def broken_files(library: Page) -> None:
    """An album with a file that a box found damaged, given to the page as data: nothing is broken here."""
    report = {"box": "b1", "name": "Kinderzimmer", "when": "2026-10-09T10:00:00Z", "error": "Invalid data found"}

    def mark(route: Route) -> None:
        answer = route.fetch()
        data = answer.json()
        if isinstance(data, list):
            data[0]["broken"] = 1
        else:
            data["broken"] = [{"file": "2.mp3", "library": None, "boxes": [report]}]
        route.fulfill(response=answer, json=data)

    library.route("**/api/albums", mark)
    library.route("**/api/albums/1", mark)
    expect("defekt" in tab(library, "albums"), "the list marks an album with a broken file")
    library.click(".album-open")
    library.wait_for_timeout(800)
    said = library.inner_text("#dlg-broken") if library.is_visible("#dlg-broken") else ""
    expect("2.mp3: Box Kinderzimmer meldet Fehler beim Abspielen" in said, f"the album dialog names it: {said}")
    expect(library.is_visible("#dlg-broken-forget"), "... and an admin may drop the report")
    if shots:
        shot(library, f"{shots}/library-album-broken.png")
    library.click("#album-dialog .dialog-close button")
    library.unroute("**/api/albums")
    library.unroute("**/api/albums/1")
    expect("defekt" not in tab(library, "albums"), "without a broken file no album is marked")


def old_versions(library: Page) -> None:
    """Versions given to the page as data: the table leaves out the old ones that are of no use any more."""

    def version(number: str, archived: bool, boxes: list[str]) -> dict[str, object]:
        return {
            "version": number, "state": "use", "by": "release", "when": "2026-10-01T10:00:00Z", "why": "", "note": "",
            "offered": True, "archived": archived, "boxes": boxes, "prepared": [],
        }  # fmt: skip

    def given(route: Route) -> None:
        answer = route.fetch()
        data = answer.json()
        box = data["boxes"][0]["id"] if data["boxes"] else "b1"
        data["versions"] = [version("0.1.0", False, []), version("0.2.0", False, [box]), version("0.3.0", True, [])]
        route.fulfill(response=answer, json=data)

    library.route("**/api/library/overview", given)
    tab(library, "versions")
    shown = library.inner_text("#versions-body")
    expect("0.1.0" not in shown and "0.2.0" in shown and "0.3.0" in shown, "an old version is hidden")
    expect("1 Version ausgeblendet" in library.inner_text("#versions-hidden"), "... and the page says how many")
    library.uncheck("#versions-hide-old")
    library.wait_for_timeout(800)
    expect("0.1.0" in library.inner_text("#versions-body"), "... and shown when the filter is switched off")
    expect(library.inner_text("#versions-hidden") == "", "... with nothing hidden")
    library.check("#versions-hide-old")
    library.wait_for_timeout(800)
    library.unroute("**/api/library/overview")


def versions(library: Page) -> None:
    """The tab with the versions of the box software."""
    tab(library, "versions")
    expect(
        "Bibliothek" in library.inner_text("#versions-body"), "the table of the versions names the one of the library"
    )
    library.fill('#releases-keep-form input[name="versions"]', "3")
    library.click('#releases-keep-form button[type="submit"]')
    library.wait_for_timeout(800)
    expect(
        library.input_value('#releases-keep-form input[name="versions"]') == "3",
        "... and the number of versions the archive keeps is set there",
    )
    library.click("#releases-check")
    library.wait_for_timeout(1000)
    expect(library.inner_text("#toast"), "looking for a release answers")
    old_versions(library)


def add_a_box(library: Page) -> None:
    """The section "Add a box" of the tab "Boxes": the line for a new box, with the pin and the address of the page."""
    text = tab(library, "boxes")
    expect("Box hinzufügen" in text, "the tab Boxes has the section Add a box")
    expect(not library.is_visible("#install-line"), "... without a version with an installer there is no line")

    def given(route: Route) -> None:
        answer = route.fetch()
        data = answer.json()
        data["install"] = {**data["install"], "version": "0.38.0", "newest": "0.39.0"}
        route.fulfill(response=answer, json=data)

    library.route("**/api/library/overview", given)
    tab(library, "versions")
    tab(library, "boxes")
    line = library.inner_text("#install-line")
    origin = library.evaluate("window.location.origin")
    expect(line.startswith("curl -fsSLk --pinnedpubkey 'sha256//"), f"the line pins the certificate: {line}")
    expect(
        line.endswith(f"{origin}/install.sh | sudo bash -s -- --library {origin}"), "... with the address of the page"
    )
    help_text = library.inner_text("#install-help")
    expect("0.38.0" in help_text and "0.39.0" in help_text, f"... and says which version it installs: {help_text}")
    if shots:
        shot(library, f"{shots}/library-add-a-box.png")
    library.unroute("**/api/library/overview")


def pair_phone(content: str) -> None:
    """What the app does with the QR code: the handshake with the one-time password (tests/sync/test_app.py)."""
    query = parse_qs(urlsplit(content).query)
    url, password, box_id = query["library"][0], query["password"][0], "bphone01"
    nonce = handshake.new_nonce()
    first = Client(url)
    nonce_library = first.post("/api/sync/boxes", {"box": box_id, "nonce": nonce})["nonce"]
    certificate = first.peer_certificate or ""
    proof = handshake.proof(password, "box", box_id, nonce, nonce_library, certificate)
    Client(url, certificate=certificate).post("/api/sync/boxes", {"box": box_id, "proof": proof, "name": "Pixel 9a"})


def add_a_phone(library: Page) -> None:
    """The section "Add a phone" of the tab "Boxes": the app, the QR code for pairing, and a phone in the table with
    its volume limits."""
    text = tab(library, "boxes")
    expect("Telefon hinzufügen" in text, "the tab Boxes has the section Add a phone")
    expect(not library.is_visible("#phone-app-link"), "... without an app in the library there is no link")
    with library.expect_response("**/api/library/pairings") as response:
        library.click("#phone-pair")
    content = response.value.json()["content"]
    library.wait_for_timeout(1500)
    expect(library.is_visible("#phone-pair-qr svg"), "Pair shows a QR code")
    expect("Noch 9:5" in library.inner_text("#phone-pair-state"), "... valid for 10 minutes")
    expect(content.startswith(f"tinyjukebox:pair?library={quote(LIBRARY.rstrip('/'), safe='')}"), f"... {content}")

    def given(route: Route) -> None:
        answer = route.fetch()
        route.fulfill(response=answer, json={**answer.json(), "app": {"version": "0.40.0"}})

    library.route("**/api/library/overview", given)
    tab(library, "versions")
    tab(library, "boxes", 1500)
    expect(library.is_visible("#phone-app-qr svg"), "with an app the section has the link and its QR code")
    expect(library.inner_text("#phone-app-link").endswith("/app.apk"), "... the link to the APK")
    if shots:
        shot(library, f"{shots}/library-add-a-phone.png")
    library.unroute("**/api/library/overview")
    pair_phone(content)
    rows = tab(library, "boxes", 1500)
    expect("Pixel 9a" in rows and "Telefon · bphone01" in rows, f"the phone is in the table as a phone: {rows[:300]}")
    library.click('#boxes-body button:has-text("Lautstärke")')
    expect(library.input_value('#phone-limits-form input[name="speaker"]') == "100", "its limits start at 100 %")
    library.fill('#phone-limits-form input[name="speaker"]', "60")
    library.fill('#phone-limits-form input[name="headphones"]', "40")
    library.click('#phone-limits-form button[type="submit"]')
    library.wait_for_timeout(1500)
    rows = library.inner_text("#boxes-body")
    expect("Lautsprecher höchstens 60 %, Kopfhörer höchstens 40 %" in rows, f"... and are set in the dialog: {rows}")
    library.once("dialog", lambda dialog: dialog.accept())
    library.click('#boxes-body tr:has-text("Pixel 9a") button:has-text("Entfernen")')
    library.wait_for_timeout(1500)


def several_boxes(library: Page) -> None:
    """The statistics of a library with two boxes, given to the page as data: all boxes, then one of them."""

    def counted(boxes: list[str]) -> dict[str, object]:
        names = {"b1": "Anna", "b2": "Kinderzimmer"}
        seconds = {"b1": 3600, "b2": 600}
        return {
            "tags": [
                {
                    "uid": "04:01", "name": "Band", "album": "Band 9", "plays": len(boxes), "finished": 1,
                    "seconds": sum(seconds[box] for box in boxes), "last": "2026-10-06T15:00:00Z",
                    "boxes": [{"id": box, "name": names[box], "seconds": seconds[box]} for box in boxes],
                }
            ],
            "never": [],
            "days": [
                {
                    "day": "2026-10-06", "box": box, "name": names[box], "seconds": seconds[box], "headphones": 0,
                    "speaker": seconds[box], "hours": {"7": seconds[box]},
                }
                for box in boxes
            ],
            "unknown_time": 0,
            "stations": [],
            "skipped": [],
            "versions": [],
        }  # fmt: skip

    asked: list[str] = []

    def answer(route: Route) -> None:
        box = route.request.url.partition("?box=")[2]
        asked.append(box)
        boxes = [
            {"id": "b1", "name": "Anna", "seconds": 3600, "plays": 1, "last": "2026-10-06T15:00:00Z"},
            {"id": "b2", "name": "Kinderzimmer", "seconds": 600, "plays": 1, "last": None},
        ]
        route.fulfill(json={**counted([box] if box else ["b1", "b2"]), "boxes": boxes, "box": box or None})

    library.route("**/api/library/statistics*", answer)
    everything = tab(library, "statistics", 1000)
    expect(library.is_visible("#stats-box"), "with several boxes the statistics offer the boxes")
    expect("Insgesamt: 1 h 10 min" in everything, f"... all boxes first, with their total: {everything[:160]}")
    expect("Anna 1 h 00 min, Kinderzimmer 10 min" in everything, "... a tag names its boxes")
    expect(library.is_visible("#stats-boxes-card"), "... the boxes have a table")
    days = " ".join(library.inner_text("#stats-days").split())
    expect(days.count("7–8 Uhr") == 2 and "Kinderzimmer 10 min" in days, f"... and a day is the day of a box: {days}")
    if shots:
        shot(library, f"{shots}/library-statistics-boxes.png")
    library.click('#stats-boxes button:has-text("Kinderzimmer")')
    library.wait_for_timeout(1000)
    one = " ".join(library.inner_text("#tab-statistics").split())
    expect(asked[-1] == "b2" and library.input_value("#stats-box") == "b2", f"a click on a box chooses it: {asked}")
    expect("Insgesamt: 10 min" in one, f"... it alone: {one[:200]}")
    expect(not library.is_visible("#stats-days td.stats-several"), "... without the columns that name a box")
    expect(not library.is_visible("#stats-boxes-card"), "... without the table of the boxes")
    if shots:
        shot(library, f"{shots}/library-statistics-box.png")
    library.select_option("#stats-box", "")
    library.wait_for_timeout(800)
    expect(asked[-1] == "" and library.is_visible("#stats-boxes-card"), "... and all boxes can be chosen again")
    library.unroute("**/api/library/statistics*")
    add_a_box(library)
    add_a_phone(library)


def tags_in_browser(library: Page, box: Page) -> None:
    """The tab "Tags" of the library and of the box: a tag placed in the browser plays, and the filter and the list."""
    expect("album" in tab(library, "tags"), "... and its tags")
    tab(box, "tags")
    box.click('.deck-card:has-text("album")')
    expect("Album aktiv" in tab(box, "now", 2500), "a tag placed in the browser plays")
    box.wait_for_timeout(3500)
    tab(box, "tags")
    box.click("#rfid-remove")
    box.wait_for_timeout(3000)
    tags_filter(box)


def tags_filter(box: Page) -> None:
    """The tab "Tags": the filter and the list, and that the page keeps the choice."""
    tab(box, "tags")
    box.click('#deck-filter button[data-filter="radio"]')
    expect(box.query_selector(".deck-card") is None, 'the filter "Radio" hides the tag of an album')
    box.click('#deck-filter button[data-filter="album"]')
    expect(box.query_selector(".deck-card") is not None, '... "Albums" shows it')
    box.click('#deck-view button[data-view="list"]')
    expect(
        box.query_selector(".deck-row") is not None and box.query_selector(".deck-card") is None,
        "... as a row in the list",
    )
    if shots:
        shot(box, f"{shots}/box-tags-list.png")
    box.reload()
    box.wait_for_timeout(1500)
    expect(box.query_selector(".deck-row") is not None, "the page keeps the choice")
    box.click('#deck-filter button[data-filter="all"]')
    box.click('#deck-view button[data-view="tiles"]')
    expect(box.query_selector(".deck-card") is not None, "... and shows the tiles again")


def sorted_albums(library: Page) -> None:
    """The list of the albums in every order, with albums given to the page as data."""

    def album(number: int, title: str, tag_name: str | None, length: float | None, created: str) -> dict[str, object]:
        tags = [{"uid": f"04:0{number}", "name": tag_name}] if tag_name is not None else []
        return {"id": str(number), "title": title, "track_count": 3, "length": length, "created": created, "tags": tags}

    albums = [
        album(1, "Zebra", "Affe", 600, "2024-05-01T10:00:00.000000Z"),
        album(2, "band 10", "Zwerg", None, "2020-01-01T10:00:00.000000Z"),
        album(3, "Band 9", None, 60, "2020-01-01T10:00:00.000000Z"),
        album(4, "Ärger", "", 7200, "2026-10-09T10:00:00.000000Z"),
    ]
    library.route("**/api/albums", lambda route: route.fulfill(json=albums))
    tab(library, "albums")

    def order(sort: str, turn: bool = False) -> str:
        """The titles in the order of a click on the heading of the column; a second click turns the order round."""
        library.click('#albums-head th[data-sort="folder"]')  # another column first: the click on this one is the first
        library.click(f'#albums-head th[data-sort="{sort}"]')
        if turn:
            library.click(f'#albums-head th[data-sort="{sort}"]')
        return " ".join(cell.inner_text().strip() for cell in library.query_selector_all("#albums-list .album-open"))

    expect(order("title") == "Ärger Band 9 band 10 Zebra", "the albums are sorted by title")
    expect(order("title", turn=True) == "Zebra band 10 Band 9 Ärger", "... and the other way round")
    expect(order("tag") == "Zebra band 10 Ärger Band 9", "... by tag, albums without a named tag last")
    expect(order("length") == "Band 9 Zebra Ärger band 10", "... by playing time, an unknown one last")
    expect(order("length", turn=True) == "Ärger Zebra Band 9 band 10", "... the longest first, an unknown one last")
    expect(order("tracks") == "Ärger Band 9 band 10 Zebra", "... by the number of tracks, then by title")
    expect(order("created") == "Ärger Zebra band 10 Band 9", "... by the day they were added, the newest first")
    expect(order("created", turn=True) == "band 10 Band 9 Zebra Ärger", "... and the oldest first")
    expect("1.5.2024" in library.inner_text("#albums-list"), "... with the day in the list")
    if shots:
        shot(library, f"{shots}/library-albums-sorted.png")
    library.unroute("**/api/albums")
    library.click('#albums-head th[data-sort="title"]')


def station_cover(library: Page, box: Page) -> None:
    """A station gets a cover on the page of the library; the box shows it."""
    tab(library, "radio")
    library.fill("#radio-new input[name=name]", "FM4")
    library.fill("#radio-new input[name=url]", "https://example.org/fm4.m3u")
    library.click("#radio-new button[type=submit]")
    library.wait_for_timeout(800)
    image = {"name": "fm4.png", "mimeType": "image/png", "buffer": base64.b64decode(PNG)}
    library.set_input_files("#radios-body input[type=file]", image)
    library.wait_for_timeout(1500)
    expect(library.query_selector("#radios-body .thumb img"), "a station gets a cover in the library")
    if shots:
        shot(library, f"{shots}/library-radio-cover.png")
    tab(box, "radio", 3000)
    expect(box.query_selector("#radios-body .thumb img"), "... and the box shows it")


def unlinked(box: Page) -> None:
    """The box leaves the library on its page: the question comes first, then the box works for itself."""
    tab(box, "system", 1500)
    expect(LIBRARY.rstrip("/") in box.inner_text("#ver-source"), "the section Version names the library as source")
    asked: list[str] = []

    def answer(dialog: Any) -> None:
        asked.append(dialog.message)
        dialog.accept()

    box.once("dialog", answer)
    box.click("#lib-unlink")
    box.wait_for_timeout(1500)
    expect(asked and "Verbindung zur Bibliothek lösen?" in asked[0], f"unlinking asks first: {asked}")
    expect("für sich allein" in box.inner_text("#lib-url"), "the box is unlinked on its page")
    expect(not box.is_visible("#lib-unlink"), "... and offers no unlinking any more")
    expect("keine Bibliothek" in box.inner_text("#ver-source"), "... and the section Version names no library")
    own = tab(box, "statistics", 1500)
    expect("Ohne Bibliothek merkt sich die Box nur die letzten 1000" in own, f"... and counts for itself: {own[:200]}")
    if shots:
        shot(box, f"{shots}/box-system-unlinked.png")


def connected(library: Page, box: Page) -> None:
    tab(box, "system", 2500)
    expect(box.inner_text("#lib-state") == "verbunden", "the box is connected to the library")
    boxes = tab(library, "boxes", 1500)
    expect(__version__ in boxes and "Umbenennen" in boxes, "the box appears in the library")
    albums = tab(library, "albums")
    expect("short" in albums, "the albums of the box are in the library")
    expect(re.search(r"short 3 \d+ (s|min)", albums), f"... with their tracks and playing time: {albums}")
    if shots:
        shot(library, f"{shots}/library-albums-connected.png")
    station_cover(library, box)
    tags_in_browser(library, box)
    statistics = tab(library, "statistics", 1500)
    expect("album short 1×" in statistics and "Nie aufgelegt" in statistics, "the statistics show what was played")
    expect("tage installiert" in statistics.lower(), "... and how long a version was installed and ran")
    library.click('#stats-tags-head th[data-sort="name"]')
    expect(
        library.get_attribute('#stats-tags-head th[data-sort="name"]', "aria-sort") == "ascending",
        "... sorted by the column that is clicked",
    )
    expect(not library.is_visible("#stats-box"), "... and with one box there is no box to choose")
    own = tab(box, "statistics", 1500)
    expect("album short 1×" in own and "Gezählt wird in der Bibliothek" in own, f"the box shows its own: {own[:200]}")
    expect("tage installiert" in own.lower() and "Insgesamt:" in own, "... with its days, versions and total")
    if shots:
        shot(box, f"{shots}/box-statistics-connected.png")
    several_boxes(library)
    broken_files(library)
    sorted_albums(library)
    versions(library)
    tab(library, "boxes")
    library.click("#token-new")
    library.wait_for_timeout(800)
    expect(len(library.inner_text("#token-value")) > 20, "a token for scripts is shown")
    library.on("dialog", lambda dialog: dialog.accept("Wohnzimmer"))
    library.click('#boxes-body button:has-text("Umbenennen")')
    library.wait_for_timeout(2500)
    expect("Wohnzimmer" in library.inner_text("#boxes-body"), "the box is renamed in the library")
    tab(box, "system", 2500)
    expect("Wohnzimmer" in box.inner_text("#lib-box"), "... and the page of the box shows the new name")
    asked = []
    box.once("dialog", lambda dialog: (asked.append(dialog.default_value), dialog.accept("Kinderzimmer")))
    box.click("#lib-rename")
    box.wait_for_timeout(2500)
    expect(asked == ["Wohnzimmer"], f"renaming on the page of the box starts from the name it has: {asked}")
    expect("Kinderzimmer" in box.inner_text("#lib-box"), "... and the box is renamed there")
    expect(
        box.inner_text("#place-name") == "Kinderzimmer" and box.title() == "Kinderzimmer · Tiny Jukebox",
        f"... the top bar and the title of the page carry the new name: {box.title()}",
    )
    if shots:
        shot(library, f"{shots}/library-boxes-connected.png")
        shot(box, f"{shots}/box-system-connected.png")
    refused(box)
    english(library, "library", "Tiny Jukebox Library")
    english(box, "box", "Kinderzimmer · Tiny Jukebox")
    unlinked(box)


def idle_logout(box: Page) -> None:
    """Nothing is done on the page for longer than the limit of the server: the page logs out and says why. The limit
    is 30 minutes; the page is told a short one."""
    tab(box, "now")
    box.evaluate("session.idle_timeout = 3; lastInput = Date.now(); localStorage.removeItem('tinyjukebox.active')")
    box.wait_for_timeout(2000)
    expect(box.is_visible("#app-view"), "the page stays as long as the limit is not reached")
    box.wait_for_timeout(8000)
    expect(
        box.is_visible("#login-view") and not box.is_visible("#app-view"), "a page on which nothing is done logs out"
    )
    said = box.inner_text("#login-error")
    expect("abgemeldet" in said, f"... and says why: {said}")
    expect(box.evaluate("fetch('/api/session').then((answer) => answer.status)") == 401, "... the session is ended")


def main() -> None:
    global shots
    step, shots = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://localhost:19222")
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 1000})
        library, box = context.new_page(), context.new_page()
        watch(library, "library")
        watch(box, "box")
        login(library, LIBRARY)
        login(box, BOX)
        {"connect": connect, "connected": connected}[step](library, box)
        if step == "connected":
            idle_logout(box)
        context.close()
    if problems:
        sys.exit("\n".join(["", "what did not work:", *problems]))


if __name__ == "__main__":
    main()
