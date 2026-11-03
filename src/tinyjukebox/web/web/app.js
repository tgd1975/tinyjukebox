"use strict";

// ---------------------------------------------------------------------------------------------------------------
// helpers

const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key in el && key !== "list") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

// ---------------------------------------------------------------------------------------------------------------
// texts: the page has none of its own. They are in texts/<language>.json, one file per language; index.html names
// the key of a text (data-t, data-t-title, ...) and the code asks for it with t()

const LANGUAGES = { de: "Deutsch", en: "English" };
const LANGUAGE_KEY = "tinyjukebox-language";

// the language chosen on this device - the browser keeps it, for this address. English until one is chosen
function chosenLanguage() {
  try {
    const chosen = localStorage.getItem(LANGUAGE_KEY);
    return Object.keys(LANGUAGES).includes(chosen) ? chosen : "en";
  } catch {
    return "en";  // a browser that keeps nothing
  }
}
const language = chosenLanguage();
let texts = {};
let pluralRules = null;

// the text of a key; {name} stands for the value of that name. A text that depends on a number has one entry per
// form ("one", "other") and takes the form of the value n
function t(key, values = {}) {
  let text = texts[key];
  if (text === undefined) return key;
  if (typeof text === "object") text = text[pluralRules.select(values.n)] || text.other;
  return text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? values[name] : all));
}

// the text of a name within a group of keys (a state, an output) - the name itself if the group has no text for it
function tOf(group, name, values) {
  const key = `${group}.${name}`;
  return key in texts ? t(key, values) : name;
}

const known = (group, name) => Boolean(name) && `${group}.${name}` in texts;

// what the page says about a request that the server refused, or about a result with an error: its own text for the
// code of the error, otherwise the English text of the server (errors.py)
function errorText(data, status) {
  if (data && known("error", data.code)) return tOf("error", data.code, data);
  return (data && data.error) || t("error.status", { status });
}

const percent = (n) => t("common.percent", { n });

// a QR code of the library (an svg) as an image: an svg in an img runs nothing and loads nothing - the page never
// puts markup of the server into the page
const qrImage = (svg, label) =>
  h("img", { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, alt: label });

// the nodes of a fixed text: {name} is the element of the page with that name (data-part), and <b>, <code> and
// <strong> become elements. Nothing is ever put into the page as html
function textNodes(text, parts) {
  const nodes = [];
  for (const piece of text.split(/(\{\w+\}|<(?:b|code|strong)>.*?<\/(?:b|code|strong)>)/)) {
    const part = /^\{(\w+)\}$/.exec(piece);
    const emphasis = /^<(\w+)>(.*)<\/\w+>$/.exec(piece);
    if (part && parts[part[1]]) nodes.push(parts[part[1]]);
    else if (emphasis) nodes.push(h(emphasis[1], {}, emphasis[2]));
    else if (piece) nodes.push(piece);
  }
  return nodes;
}

const TEXT_ATTRIBUTES = { title: "title", label: "aria-label", placeholder: "placeholder", confirm: "data-confirm" };

// gives every element of index.html its text and the attributes that are texts
function fillTexts() {
  for (const el of document.querySelectorAll("[data-t]")) {
    const parts = {};
    for (const part of el.querySelectorAll(":scope > [data-part]")) parts[part.dataset.part] = part;
    el.replaceChildren(...textNodes(t(el.dataset.t), parts));
  }
  for (const [name, attribute] of Object.entries(TEXT_ATTRIBUTES)) {
    for (const el of document.querySelectorAll(`[data-t-${name}]`)) {
      el.setAttribute(attribute, t(el.getAttribute(`data-t-${name}`)));
    }
  }
}

// the choice of the language, wherever index.html has one: the browser keeps a change and the page loads again
function fillLanguages() {
  for (const select of document.querySelectorAll("select.language")) {
    select.replaceChildren(...Object.entries(LANGUAGES).map(([code, name]) =>
      h("option", { value: code, selected: code === language }, name)));
    select.addEventListener("change", () => {
      try {
        localStorage.setItem(LANGUAGE_KEY, select.value);
      } catch {
        return;
      }
      location.reload();
    });
  }
}

async function loadTexts() {
  texts = await (await fetch(`texts/${language}.json`, { cache: "no-cache" })).json();
  pluralRules = new Intl.PluralRules(language);
  document.documentElement.lang = language;
  fillTexts();
  fillLanguages();
}

function formatTime(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return "–";
  seconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

// how the badge of a state looks - its text is in the group "state"
const STATE_CLASSES = {
  wait_for_tag: "idle", idle_long: "idle", check_tag: "busy", play_album: "ok", unknown_tag: "warn",
  unknown_album: "warn", broken_album: "error", test_radio: "busy", fetch_album: "busy", blocked: "error",
  spotify_speaker: "ok", end: "idle",
};
const PAIRING_RESULTS = ["paired", "not_found", "failed"];
const pairingResult = (result) => (PAIRING_RESULTS.includes(result) ? tOf("bluetooth.pairing", result) : "");

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("toast-error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3000);
}

// ---------------------------------------------------------------------------------------------------------------
// api

class ApiError extends Error {
  constructor(status, data) {
    super(errorText(data, status));
    this.status = status;
    this.data = data;
  }
}

// when the user did something on the page last - in any tab of this browser, hence the storage. The server ends a
// session in which nothing was done for too long (rest-api/idle-timeout); the page asks all the time by itself, so
// every request says how long ago that was
const ACTIVE_KEY = "tinyjukebox.active";
let lastInput = Date.now();
let lastInputStored = 0;
let uploadsRunning = 0;  // while files are sent the user waits for the page: that is no absence

function noteInput() {
  lastInput = Date.now();
  if (lastInput - lastInputStored < 5000) return;
  lastInputStored = lastInput;
  try {
    localStorage.setItem(ACTIVE_KEY, String(lastInput));
  } catch { /* a browser without storage: this tab counts for itself */ }
}
for (const type of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"]) {
  document.addEventListener(type, noteInput, { passive: true, capture: true });
}

function idleSeconds() {
  if (uploadsRunning) return 0;
  let last = lastInput;
  try {
    last = Math.max(last, Number(localStorage.getItem(ACTIVE_KEY)) || 0);
  } catch { /* see above */ }
  return Math.max(0, Math.round((Date.now() - last) / 1000));
}

const requestHeaders = () => ({ "X-TinyJukebox-Request": "1", "X-TinyJukebox-Idle": String(idleSeconds()) });

// the answer of the server: { status, data }
async function request(method, path, body) {
  const options = { method, credentials: "same-origin", headers: requestHeaders() };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  if (response.status !== 204) {
    const text = await response.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
  }
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return { status: response.status, data };
}

const api = async (method, path, body) => (await request(method, path, body)).data;

// runs an action and reports errors as toast
async function run(action, successMessage) {
  try {
    const result = await action();
    if (successMessage) toast(successMessage);
    return result;
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) toast(e.message, true);
    return undefined;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// identity: the library, or which box - the login page shows it already, and a box has colors of its own (hue from
// its name or its id, style.css)

let identity = null;

async function loadIdentity() {
  try {
    identity = await api("GET", "/api/identity");
  } catch {
    return;  // the page keeps what it knows
  }
  showIdentity();
}

function showIdentity() {
  const library = identity ? identity.mode === "library" : isLibrary();
  const name = identity && !library ? identity.name : null;
  const hued = Boolean(identity) && Number.isInteger(identity.hue);
  const root = document.documentElement;
  root.classList.toggle("box-hued", hued);
  if (hued) root.style.setProperty("--box-hue", identity.hue);
  else root.style.removeProperty("--box-hue");
  const place = $("login-place");
  place.textContent = library ? t("login.place.library")
    : name ? t("login.place.box_named", { name }) : t("login.place.box");
  place.hidden = !identity;
  $("place-name").textContent = name || "";
  $("place-name").hidden = !name;
  document.title = library ? t("title.library") : name ? `${name} · Tiny Jukebox` : "Tiny Jukebox";
}

// ---------------------------------------------------------------------------------------------------------------
// login / session

let session = null;

// the app for phones shows this page in expert mode and offers it an object of its own: the NFC of the phone for
// "Scan tag", and the news that the page logged in or out (docs/developer/app.md)
const APP = window.TinyJukeboxApp || null;

// the limit of the server in seconds, 0 for none
const idleLimit = () => (session && session.idle_timeout) || 0;

function showLogin() {
  // the server ended the session, or the page did: say why if nothing was done for too long
  if (APP && session) APP.postMessage("logout");  // the app goes back to simple mode
  const limit = idleLimit();
  $("login-error").textContent = limit && idleSeconds() >= limit - 10
    ? t("login.idle", { minutes: Math.round(limit / 60) }) : "";
  session = null;
  stopUpdates();
  stopScan();
  $("app-view").hidden = true;
  $("login-view").hidden = false;
}

const isLibrary = () => Boolean(session && session.mode === "library");

function showApp(user) {
  session = user;
  if (APP) APP.postMessage(`session:${user.role}`);
  document.body.classList.toggle("is-admin", user.role === "admin");
  document.body.classList.toggle("is-library", user.mode === "library");
  showIdentity();
  $("user-name").textContent = `${user.username} (${user.role})`;
  $("sys-user").textContent = `${user.username} (${user.role})`;
  $("own-password").username.defaultValue = user.username;  // for password managers - survives form.reset()
  const canScan = user.mode === "library" && ("NDEFReader" in window || ON_A_PHONE || Boolean(APP));
  // a folder cannot be chosen in the app: files are
  $("upload-folder").closest("label").hidden = Boolean(APP);
  $("scan-toggle").hidden = !canScan;
  $("tag-new-card").hidden = user.role !== "admin" && !canScan;
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  // the library has the statistics next to its boxes; on a box they come after what the box plays and knows
  if (!isLibrary()) $("tab-button-upload").before($("tab-button-statistics"));
  selectTab(location.hash.slice(1) || (isLibrary() ? "boxes" : "now"));
  if (!isLibrary() && user.role === "admin") loadVersion();  // a release that waits for an answer: on every tab
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  $("login-error").textContent = "";
  try {
    const user = await api("POST", "/api/login", {
      username: form.username.value, password: form.password.value,
    });
    delete user.token;  // the cookie carries the session - the page keeps no token (rest-api.md, section 2)
    form.password.value = "";
    showApp(user);
  } catch (e) {
    $("login-error").textContent = e.status === 429
      ? t("login.error.too_many") : e.status === 401 ? t("login.error.wrong") : e.message;
  }
});

$("logout").addEventListener("click", async () => {
  await run(() => api("POST", "/api/logout"));
  showLogin();
});

// nothing was done for too long: the page logs out by itself and does not wait for the next answer of the server
setInterval(() => {
  const limit = idleLimit();
  if (!limit || idleSeconds() < limit) return;
  fetch("/api/logout", { method: "POST", credentials: "same-origin", headers: { "X-TinyJukebox-Request": "1" } })
    .catch(() => { /* the server ends the session by itself */ });
  showLogin();
}, 5000);

// ---------------------------------------------------------------------------------------------------------------
// tabs

let currentTab = "now";
const tabLoaders = {
  now: () => refreshStatus(),
  tags: () => loadDeck(),
  log: async () => { if (isLibrary()) await logSources(); return loadLog(true); },
  "tag-management": () => loadTags(),
  albums: () => loadAlbums(),
  radio: () => loadRadios(),
  spotify: () => loadSpotify(),
  bluetooth: () => refreshStatus(),
  upload: () => loadUpload(),
  system: () => loadSystem(),
  boxes: () => loadBoxes(),
  versions: () => loadVersions(),
  statistics: () => loadStatistics(),
};
const BOX_TABS = ["now", "bluetooth"];        // what only the page of a box has
const LIBRARY_TABS = ["boxes", "versions"];   // ... and only the page of the library

function selectTab(name) {
  const home = isLibrary() ? "boxes" : "now";
  if (!Object.hasOwn(tabLoaders, name) || (isLibrary() ? BOX_TABS : LIBRARY_TABS).includes(name)) name = home;
  if (["upload", "log"].includes(name) && session && session.role !== "admin") name = home;
  if (name !== "tag-management") {
    stopScan();
    $("scan-refused").hidden = true;
  }
  currentTab = name;
  for (const button of document.querySelectorAll(".tabs button")) {
    button.classList.toggle("active", button.dataset.tab === name);
    button.setAttribute("aria-selected", button.dataset.tab === name);
  }
  for (const section of document.querySelectorAll(".tab")) section.hidden = section.id !== `tab-${name}`;
  history.replaceState(null, "", `#${name}`);
  tabLoaders[name]();
  if (!events) startUpdates();
}

for (const button of document.querySelectorAll(".tabs button")) {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
}

// ---------------------------------------------------------------------------------------------------------------
// now playing

let events = null;
let pollTimer = null;
let lastStatus = null;
let lastStatusTime = 0;
let shownAlbumKey = null;
let volumeDragging = false;
let albumsCache = [];
let radiosCache = [];
let spotifyCache = [];

// the server pushes status changes and new log records (server-sent events, /api/events); while the stream is
// down, the page falls back to polling once per second
function startUpdates() {
  stopUpdates();
  if (!window.EventSource) {
    startPolling();
    return;
  }
  const source = new EventSource("/api/events");
  events = source;
  source.addEventListener("open", stopPolling);
  source.addEventListener("error", () => {
    startPolling();
    // closed for good (e.g. 401 after logout) - the polling shows the login; retry the stream later
    if (source.readyState === EventSource.CLOSED && events === source) {
      events = null;
      setTimeout(() => { if (events === null && session) startUpdates(); }, 10000);
    }
  });
  source.addEventListener("status", (event) => applyStatus(JSON.parse(event.data)));
  source.addEventListener("log", () => {
    if (currentTab === "log" && $("log-follow").checked) loadLog(false);
  });
}

function stopUpdates() {
  if (events) events.close();
  events = null;
  stopPolling();
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    if (document.hidden) return;
    refreshStatus();
    if (currentTab === "log" && $("log-follow").checked) loadLog(false);
  }, 1000);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function applyStatus(status) {
  lastStatus = status;
  lastStatusTime = performance.now();
  if (!isLibrary()) renderStatus(status);  // nothing plays on a library
}

async function refreshStatus() {
  try {
    applyStatus(await api("GET", "/api/status"));
  } catch (e) {
    if (e.status !== 401) $("now-state").textContent = t("now.connection_lost");
  }
}

function imageElement(src, placeholderClass = "cover-placeholder", placeholder = "♪") {
  if (!src) return h("span", { class: placeholderClass }, placeholder);
  const img = h("img", { src, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(h("span", { class: placeholderClass }, placeholder)));
  return img;
}

// covers changed on this page: the browser keeps a cover for a while, so a new one gets another address
const coverVersions = {};

function coverElement(albumId, placeholderClass = "cover-placeholder") {
  const version = coverVersions[albumId] ? `?v=${coverVersions[albumId]}` : "";
  return imageElement(`/api/albums/${encodeURIComponent(albumId)}/cover${version}`, placeholderClass);
}

// cover of a radio station (an entry of /api/radios or the station of the status); its address names the image, so
// a new cover is never the one the browser kept
function radioCoverElement(radio, placeholderClass = "cover-placeholder") {
  const src = radio && radio.cover
    ? `/api/radios/${encodeURIComponent(radio.id)}/cover?v=${encodeURIComponent(radio.cover)}` : null;
  return imageElement(src, placeholderClass, "📻");
}

// cover of an entry of Spotify (an entry of /api/spotify or of the status), named like the cover of a station
function spotifyCoverElement(entry, placeholderClass = "cover-placeholder") {
  const src = entry && entry.cover
    ? `/api/spotify/${encodeURIComponent(entry.id)}/cover?v=${encodeURIComponent(entry.cover)}` : null;
  return imageElement(src, placeholderClass, "🎵");
}

// sends the image file as the cover of "/api/albums/<id>", "/api/radios/<id>" or "/api/spotify/<id>" - true if done
async function sendCover(path, file) {
  let response;
  try {
    response = await fetch(`${path}/cover`, {
      method: "PUT", credentials: "same-origin", body: file,
      headers: { ...requestHeaders(), "Content-Type": file.type || "application/octet-stream" },
    });
  } catch {
    toast(t("cover.send_failed"), true);
    return false;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(errorText(data, response.status), true);
    return false;
  }
  return true;
}

// makes the image file the cover of the album - true if done
async function uploadCover(albumId, file) {
  if (!(await sendCover(`/api/albums/${encodeURIComponent(albumId)}`, file))) return false;
  coverVersions[albumId] = Date.now();
  return true;
}

// the cover of the album, the radio station or the entry of Spotify of the tag
function tagImage(tag) {
  if (tag.album_id && tag.album_exists) return coverElement(tag.album_id);
  if (tag.radio_id && tag.radio_exists) return radioCoverElement(radiosCache.find((r) => r.id === tag.radio_id));
  if (tag.spotify_id && tag.spotify_exists) return spotifyCoverElement(spotifyCache.find((e) => e.id === tag.spotify_id));
  return imageElement(null);
}

const LED_COLORS = {
  BLACK: "#222", WHITE: "#fff", RED: "#e53935", GREEN: "#43a047", BLUE: "#1e88e5", YELLOW: "#fdd835",
  AQUA: "#26c6da", MAGENTA: "#d81b60",
};

let ledSequence = null;  // timer of a led that shows more than two colors in turn

function renderLed(led) {
  const el = $("led");
  el.hidden = !led;
  clearInterval(ledSequence);
  ledSequence = null;
  if (!led) return;
  if (led.colors) {
    let step = 0;
    const show = () => {
      const color = LED_COLORS[led.colors[step++ % led.colors.length]] || "#888";
      el.style.setProperty("--led-a", color);
      el.style.setProperty("--led-b", color);
    };
    el.classList.remove("led-alternate");
    el.title = t("led.title", { colors: led.colors.join(" / ") });
    show();
    ledSequence = setInterval(show, (led.delay || 0.4) * 1000);
    return;
  }
  const a = LED_COLORS[led.color || led.color_a] || "#888";
  const b = LED_COLORS[led.color_b] || a;
  el.style.setProperty("--led-a", a);
  el.style.setProperty("--led-b", b);
  el.style.setProperty("--led-delay", `${(led.delay || 1) * 2}s`);
  el.classList.toggle("led-alternate", Boolean(led.color_b));
  el.title = t("led.title", { colors: led.color || `${led.color_a} / ${led.color_b}` });
}

let lastPresentUid = null;

function renderStatus(status) {
  renderLed(status.led);
  const presentUid = status.tag ? status.tag.uid : null;
  if (presentUid !== lastPresentUid) {
    lastPresentUid = presentUid;
    if (currentTab === "tags") loadDeck();
  }
  const radio = status.radio;
  const spotify = status.spotify;
  const spotifyTitle = spotify && (spotify.speaker ? t("now.spotify.speaker")
    : spotify.test ? t("now.spotify.test") : t("now.spotify.active"));
  const [stateText, stateClass] = radio
    ? [radio.test ? t("now.radio.test") : t("now.radio.active"), radio.state === "retrying" ? "warn" : "ok"]
    : spotify ? [spotifyTitle, spotify.state === "error" ? "error" : spotify.state === "retrying" ? "warn" : "ok"]
    : [tOf("state", status.state), STATE_CLASSES[status.state] || "idle"];
  const album = status.album;
  const badge = $("now-state");
  const playback = album && album.playback ? tOf("playback", album.playback.state)
    : radio ? tOf("radio.state", radio.state) : spotify ? tOf("spotify.state", spotify.state) : null;
  badge.textContent = playback ? `${stateText} · ${playback}` : stateText;
  badge.title = radio && radio.error ? radio.error : spotify && spotify.error ? tOf("spotify.error", spotify.error) : "";
  badge.className = `badge badge-${stateClass}`;
  $("sys-state").textContent = stateText;

  const tag = status.tag;
  const tagText = tag ? t("now.tag", { name: tag.name || t("common.unnamed"), uid: tag.uid }) : "";
  $("now-tag").textContent = tagText;
  $("now-unlink").hidden = !(tag && (tag.album_id || tag.radio_id || tag.spotify_id));
  $("sys-tag").textContent = tag ? `${tag.name || t("common.unnamed")} (${tag.uid})` : "–";

  // album header and track list are only rebuilt if the album changes
  const albumKey = album ? `${album.id}:${album.title}:${coverVersions[album.id] || ""}`
    : radio ? `radio:${radio.id}:${radio.name}:${radio.url}:${radio.cover}`
    : spotify ? `spotify:${spotify.id}:${spotify.name}:${spotify.cover}:${spotify.speaker}` : `state:${status.state}`;
  if (albumKey !== shownAlbumKey) {
    shownAlbumKey = albumKey;
    const cover = $("now-cover");
    cover.replaceChildren(album ? coverElement(album.id) : radio ? radioCoverElement(radio)
      : spotify ? spotifyCoverElement(spotify) : imageElement(null));
    $("now-title").textContent = album ? album.title : radio ? radio.name
      : spotify ? (spotify.name || t("now.spotify.speaker")) : stateText;
    $("now-artist").textContent = album ? [album.artist, album.folder].filter(Boolean).join(" · ")
      : radio ? radio.url : spotify && spotify.kind ? tOf("spotify.kind", spotify.kind) : "";
    const list = $("now-tracks");
    list.replaceChildren(...(album ? album.tracks.map((track) => h("li", { "data-index": track.index },
      h("span", { class: "track-name" }, track.title || track.filename),
      h("span", { class: "muted small" }, formatTime(track.length))
    )) : []));
    $("now-tracks-card").hidden = !album;
  }

  const hasTrack = album && album.playback && album.playback.track_id !== null;
  const spotifyTrack = spotify && spotify.track && spotify.track.title;
  $("now-track").hidden = !hasTrack && !spotifyTrack;
  if (spotifyTrack) {
    $("now-track-title").textContent = `${spotify.track.index + 1}. ${spotify.track.title}`;
    renderProgress();
  }
  if (hasTrack) {
    const track = album.tracks[album.playback.track_id];
    $("now-track-title").textContent = track ? `${track.index + 1}. ${track.title || track.filename}` : "";
    for (const li of $("now-tracks").children) {
      li.classList.toggle("current", Number(li.dataset.index) === album.playback.track_id);
    }
    renderProgress();
  }
  for (const id of ["btn-prev", "btn-next", "btn-restart"]) $(id).disabled = !album && !spotify;  // radio: no effect
  // play/pause: pauses what plays, continues what is paused - nothing to do for a finished album
  const playState = album && album.playback ? album.playback.state : radio ? radio.state : spotify ? spotify.state : null;
  const paused = playState === "paused" || playState === "elsewhere";
  const playPause = $("btn-playpause");
  playPause.disabled = !playState || playState === "finished" || playState === "error" || (radio && radio.test);
  playPause.textContent = paused ? "▶" : "⏸";
  playPause.title = paused ? t("common.resume") : t("common.pause");
  playPause.setAttribute("aria-label", playPause.title);
  playPause.dataset.action = paused ? "play" : "pause";

  const volume = status.volume;
  if (!volumeDragging) {
    const slider = $("volume");
    slider.max = volume.max_volume;
    slider.value = volume.volume;
    $("volume-value").textContent = percent(Math.round(volume.volume));
  }
  $("volume-info").textContent = t("now.volume.info", {
    output: tOf("output", volume.output), default: Math.round(volume.default_volume), max: Math.round(volume.max_volume),
  });
  renderBluetooth(status.bluetooth);

  // radio test (tab radio)
  const testing = radio && radio.test;
  $("radio-test").hidden = !testing;
  if (testing) {
    $("radio-test-text").textContent = t(radio.error ? "radio.test.state_error" : "radio.test.state", {
      station: radio.name === radio.url ? radio.url : `${radio.name} (${radio.url})`,
      state: tOf("radio.state", radio.state), error: radio.error,
    });
  }

  // test of an entry of Spotify (tab Spotify)
  const spotifyTesting = spotify && spotify.test;
  $("spotify-test").hidden = !spotifyTesting;
  if (spotifyTesting) {
    $("spotify-test-text").textContent = t("spotify.test.state", { name: spotify.name, state: tOf("spotify.state", spotify.state) });
  }

  // unknown tag on the box -> offer to link it right here
  const linkCard = $("now-link");
  const showLink = session && session.role === "admin" && status.state === "unknown_tag" && tag;
  if (showLink && linkCard.dataset.uid !== tag.uid) {
    linkCard.dataset.uid = tag.uid;
    $("now-link-uid").textContent = tag.uid;
    fillTargetSelect($("now-link-album"), null, t("target.choose"), true);
  }
  if (!showLink) delete linkCard.dataset.uid;
  linkCard.hidden = !showLink;
}

function renderBluetooth(bluetooth) {
  const device = bluetooth.device;
  $("headset-icon").hidden = !bluetooth.connected;
  $("bt-facts").hidden = !device;
  $("bt-unpair").disabled = !device;
  $("bt-pair").disabled = !bluetooth.available || bluetooth.pairing;
  $("bt-message").textContent = !bluetooth.available ? t("bluetooth.unavailable")
    : bluetooth.pairing ? t("bluetooth.searching")
      : !device ? [t("bluetooth.none"), pairingResult(bluetooth.pairing_result)].filter(Boolean).join(" ")
        : bluetooth.pairing_result !== "paired" ? pairingResult(bluetooth.pairing_result) : "";
  if (device) {
    $("bt-name").textContent = device.name;
    $("bt-address").textContent = device.address;
    $("bt-connected").textContent = bluetooth.connected ? t("common.yes") : t("common.no");
    $("bt-battery").textContent = bluetooth.battery === null ? "–" : percent(bluetooth.battery);
    $("bt-volume").textContent = bluetooth.volume === null ? "–" : percent(Math.round(bluetooth.volume));
  }
  $("bt-default").textContent = percent(Math.round(bluetooth.default_volume));
  $("bt-max").textContent = percent(Math.round(bluetooth.max_volume));
  $("bt-virtual").hidden = !bluetooth.virtual;
  $("bt-virtual-on").disabled = !device || bluetooth.connected;
  $("bt-virtual-off").disabled = !bluetooth.connected;
  for (const button of $("bt-virtual-buttons").children) button.disabled = !bluetooth.connected;
}

// the position is interpolated between two status requests
function renderProgress() {
  const album = lastStatus && lastStatus.album;
  const spotify = lastStatus && lastStatus.spotify;
  const playback = album && album.playback && album.playback.track_id !== null ? album.playback
    : spotify && spotify.track && spotify.track.title
      ? { position: spotify.track.position, length: spotify.track.length, state: spotify.state === "playing" ? "play" : spotify.state }
      : null;
  if (!playback) return;
  let position = playback.position || 0;
  if (playback.state === "play") position += (performance.now() - lastStatusTime) / 1000;
  const length = playback.length || 0;
  position = Math.min(position, length || position);
  $("now-pos").textContent = formatTime(position);
  $("now-len").textContent = formatTime(length);
  $("now-progress").style.width = length ? `${(100 * position) / length}%` : "0";
}

function animate() {
  if (currentTab === "now" && !document.hidden) renderProgress();
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);

// the arrow and volume buttons do exactly what the buttons on the box do
async function pressButton(button) {
  await run(() => api("POST", `/api/buttons/${button}`, { action: "press" }));
  setTimeout(refreshStatus, 250);
}
$("btn-prev").addEventListener("click", () => pressButton("prev"));
$("btn-next").addEventListener("click", () => pressButton("next"));
$("btn-vol-up").addEventListener("click", () => pressButton("volume-up"));
$("btn-vol-down").addEventListener("click", () => pressButton("volume-down"));
$("btn-restart").addEventListener("click", async () => {
  await run(() => api("POST", "/api/player/restart"));
  setTimeout(refreshStatus, 250);
});
$("btn-playpause").addEventListener("click", async (event) => {
  await run(() => api("POST", `/api/player/${event.currentTarget.dataset.action || "pause"}`));
  setTimeout(refreshStatus, 250);
});

// start value and maximum of the volumes (admins)
function openLimits() {
  const profiles = lastStatus && lastStatus.volume.profiles;
  if (!profiles) return;
  const form = $("limits-form");
  for (const name of ["speaker", "headphones"]) {
    form[`${name}_default`].value = Math.round(profiles[name].default_volume);
    form[`${name}_max`].value = Math.round(profiles[name].max_volume);
  }
  $("limits-dialog").showModal();
}
$("volume-limits").addEventListener("click", openLimits);
$("bt-limits").addEventListener("click", openLimits);
$("limits-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const profiles = lastStatus.volume.profiles;
  let saved = true;
  for (const name of ["speaker", "headphones"]) {
    const limits = { default_volume: Number(form[`${name}_default`].value), max_volume: Number(form[`${name}_max`].value) };
    if (limits.default_volume === profiles[name].default_volume && limits.max_volume === profiles[name].max_volume) continue;
    if (limits.default_volume > limits.max_volume) {
      toast(t("limits.default_over_max", { output: tOf("output", name) }), true);
      return;
    }
    saved = await run(() => api("PUT", `/api/volume/limits/${name}`, limits)) !== undefined && saved;
  }
  if (saved) {
    toast(t("limits.saved"));
    $("limits-dialog").close();
  }
  refreshStatus();
});

// bluetooth headset
$("bt-pair").addEventListener("click", async () => {
  await run(() => api("POST", "/api/bluetooth/pair"), t("bluetooth.pair.started"));
  refreshStatus();
});
$("bt-unpair").addEventListener("click", async () => {
  const device = lastStatus && lastStatus.bluetooth.device;
  if (!device || !confirm(t("bluetooth.unpair.confirm", { name: device.name }))) return;
  await run(() => api("DELETE", "/api/bluetooth/device"), t("bluetooth.unpaired"));
  setTimeout(refreshStatus, 250);
});
async function virtualHeadset(body) {
  await run(() => api("POST", "/api/bluetooth/virtual", body));
  setTimeout(refreshStatus, 250);
}
$("bt-virtual-on").addEventListener("click", () => virtualHeadset({ connected: true }));
$("bt-virtual-off").addEventListener("click", () => virtualHeadset({ connected: false }));
for (const button of $("bt-virtual-buttons").children) {
  button.addEventListener("click", () => virtualHeadset({ button: button.dataset.button }));
}

const volumeSlider = $("volume");
volumeSlider.addEventListener("input", () => {
  volumeDragging = true;
  $("volume-value").textContent = percent(volumeSlider.value);
});
volumeSlider.addEventListener("change", async () => {
  await run(() => api("PUT", "/api/volume", { volume: Number(volumeSlider.value) }));
  volumeDragging = false;
  refreshStatus();
});

$("now-unlink").addEventListener("click", async () => {
  const tag = lastStatus && lastStatus.tag;
  if (tag && await unlinkTag(tag)) refreshStatus();
});

$("now-link-button").addEventListener("click", async () => {
  const target = $("now-link-album").value;
  if (!target) return toast(t("target.required"), true);
  const result = await run(() => linkTag("current", target),
    target.startsWith("radio:") ? t("now.linked.radio")
      : target.startsWith("spotify:") ? t("now.linked.spotify") : t("now.linked.album"));
  if (result) refreshStatus();
});

// targets of a tag in selects: "album:<id>", "radio:<id>" or "spotify:<id>"
const targetValue = (tag) => tag.album_id ? `album:${tag.album_id}` : tag.radio_id ? `radio:${tag.radio_id}`
  : tag.spotify_id ? `spotify:${tag.spotify_id}` : "";

function linkTag(uid, target) {
  const path = `/api/tags/${encodeURIComponent(uid)}`;
  if (!target) return api("DELETE", `${path}/album`);
  const [type, id] = target.split(/:(.*)/s);
  if (type === "radio") return api("PUT", `${path}/radio`, { radio_id: id });
  if (type === "spotify") return api("PUT", `${path}/spotify`, { spotify_id: id });
  return api("PUT", `${path}/album`, { album_id: id });
}

async function loadTargets() {
  const [albums, radios, entries] = await Promise.all([run(() => api("GET", "/api/albums")),
    run(() => api("GET", "/api/radios")), run(() => api("GET", "/api/spotify"))]);
  if (albums) albumsCache = albums;
  if (radios) radiosCache = radios;
  if (entries) spotifyCache = entries;
  return Boolean(albums && radios && entries);
}

function targetOptions(selected, unmappedFirst = false) {
  const albums = [...albumsCache];
  if (unmappedFirst) albums.sort((a, b) => (a.tags.length > 0) - (b.tags.length > 0));
  const option = (value, text) => h("option", { value, selected: value === selected }, text);
  const groups = [];
  if (albums.length) groups.push(h("optgroup", { label: t("tabs.albums") }, ...albums.map((a) =>
    option(`album:${a.id}`, unmappedFirst && !a.tags.length ? t("target.album_without_tag", { title: a.title }) : a.title))));
  if (radiosCache.length) groups.push(h("optgroup", { label: t("radio.title") }, ...radiosCache.map((r) =>
    option(`radio:${r.id}`, `📻 ${r.name}`))));
  if (spotifyCache.length) groups.push(h("optgroup", { label: t("spotify.title") }, ...spotifyCache.map((e) =>
    option(`spotify:${e.id}`, `🎵 ${e.name}`))));
  return groups;
}

async function fillTargetSelect(select, selected, placeholder, unmappedFirst = false) {
  await loadTargets();
  select.replaceChildren(h("option", { value: "" }, placeholder), ...targetOptions(selected, unmappedFirst));
}

// unlinks the tag from its album or radio station (asks first) - true if done
async function unlinkTag(tag) {
  if (!confirm(t("tagman.unlink.confirm", { name: tag.name || tag.uid }))) return false;
  const result = await run(() => api("DELETE", `/api/tags/${encodeURIComponent(tag.uid)}/album`), t("tagman.unlinked"));
  return result !== undefined;
}

// the tags linked to an album or radio station - admins can unlink them (✕)
function tagChips(tags, onUnlinked) {
  if (!tags.length) return [h("span", { class: "muted small" }, t("common.no_tag"))];
  const isAdmin = session && session.role === "admin";
  return tags.map((tag) => h("span", { class: "tag-chip" }, tag.name || tag.uid,
    isAdmin ? h("button", { class: "link danger-text", title: t("common.unlink"), "aria-label": t("common.unlink"),
      onclick: async () => { if (await unlinkTag(tag)) onUnlinked(); } }, "✕") : ""));
}

// "Titel des Albums", "📻 Sender" or a hint that the target is missing
function targetText(tag) {
  if (tag.album_id) {
    const album = albumsCache.find((a) => a.id === tag.album_id);
    return album ? album.title : t("target.album_missing", { id: tag.album_id });
  }
  if (tag.radio_id) {
    const radio = radiosCache.find((r) => r.id === tag.radio_id);
    return radio ? `📻 ${radio.name}` : t("target.radio_missing", { id: tag.radio_id });
  }
  if (tag.spotify_id) {
    const entry = spotifyCache.find((e) => e.id === tag.spotify_id);
    return entry ? `🎵 ${entry.name}` : t("target.spotify_missing", { id: tag.spotify_id });
  }
  return null;
}

// input that renames a tag (admins)
function tagNameInput(tag, onSaved) {
  const input = h("input", { value: tag.name || "", class: "inline-input", placeholder: t("common.name"), "aria-label": t("common.name") });
  input.addEventListener("change", async () => {
    const saved = await run(() => api("PATCH", `/api/tags/${encodeURIComponent(tag.uid)}`, { name: input.value }),
      t("tagman.name_saved"));
    if (saved && onSaved) onSaved();
  });
  return input;
}

// select that links a tag with an album or radio station (admins)
function tagTargetSelect(tag, onLinked) {
  const select = h("select", { "aria-label": t("target.label") },
    h("option", { value: "" }, t("target.none")), ...targetOptions(targetValue(tag)));
  if ((tag.album_id && !tag.album_exists) || (tag.radio_id && !tag.radio_exists)
    || (tag.spotify_id && !tag.spotify_exists)) {
    select.append(h("option", { value: targetValue(tag), selected: true },
      t("target.missing_option", { id: tag.album_id || tag.radio_id || tag.spotify_id })));
  }
  select.addEventListener("change", async () => {
    await run(() => linkTag(tag.uid, select.value), t("tagman.link_saved"));
    onLinked();
  });
  return select;
}

// ---------------------------------------------------------------------------------------------------------------
// tags

// where a tag stopped: "finished", "track 2, 1:30", or a dash
function tagProgress(tag) {
  const activity = tag.activity;
  if (!activity) return "–";
  return activity.finished ? t("progress.finished")
    : t("progress.track", { n: activity.track_id + 1, time: formatTime(activity.track_time) });
}

async function loadTags() {
  const unmapped = $("tags-unmapped").checked;
  const [tags, targetsLoaded] = await Promise.all([
    run(() => api("GET", `/api/tags${unmapped ? "?unmapped=1" : ""}`)),
    loadTargets(),
  ]);
  if (!tags || !targetsLoaded) return;
  const isAdmin = session.role === "admin";
  const body = $("tags-body");
  body.replaceChildren(...tags.map((tag) => {
    const nameCell = isAdmin ? tagNameInput(tag, refreshScanned) : tag.name || "–";
    const albumCell = isAdmin ? tagTargetSelect(tag, tagsChanged) : targetText(tag) || "–";
    const progress = tagProgress(tag);
    const remove = isAdmin ? h("button", {
      class: "link danger-text", title: t("tagman.remove.title"), onclick: async () => {
        if (!confirm(t("tagman.remove.confirm", { name: tag.name || tag.uid }))) return;
        await run(() => api("DELETE", `/api/tags/${encodeURIComponent(tag.uid)}`), t("tagman.removed"));
        if (tag.uid === scannedUid) forgetScanned();
        loadTags();
      },
    }, t("common.remove")) : "";
    return h("tr", { class: tag.present ? "present" : "" },
      h("td", { class: "thumb-cell" }, h("div", { class: "thumb" }, tagImage(tag))),
      h("td", {}, nameCell),
      h("td", {}, h("code", {}, tag.uid), tag.present ? h("span", { class: "badge badge-ok small" }, t("common.present")) : ""),
      h("td", {}, albumCell),
      h("td", { class: "small" }, progress),
      h("td", {}, remove));
  }));
  if (!tags.length) body.append(h("tr", {}, h("td", { colSpan: 6, class: "muted" }, t("tagman.none"))));
  if (isAdmin) {
    $("tag-new-album").replaceChildren(h("option", { value: "" }, t("tagman.new.no_target")), ...targetOptions(""));
  }
}

$("tag-new").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const [type, id] = form.album_id.value.split(/:(.*)/s);
  const tag = await run(() => api("POST", "/api/tags", {
    uid: form.uid.value.trim() || undefined, name: form.name.value.trim() || undefined,
    album_id: type === "album" ? id : undefined, radio_id: type === "radio" ? id : undefined,
    spotify_id: type === "spotify" ? id : undefined,
  }));
  if (tag) {
    toast(t("tagman.created", { uid: tag.uid }));
    form.reset();
    loadTags();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// tags (the web page as rfid reader: a click places the tag on the box)

// what the tab "Tags" shows: all tags or only those of albums or of radio stations, as tiles or as a list. The choice is
// kept in this browser
const DECK_FILTERS = ["all", "album", "radio", "spotify"];
const DECK_VIEWS = ["tiles", "list"];
const DECK_KEY = "tinyjukebox.deck";
let deckChoice = { filter: "all", view: "tiles" };
try {
  const saved = JSON.parse(localStorage.getItem(DECK_KEY) || "{}");
  if (DECK_FILTERS.includes(saved.filter)) deckChoice.filter = saved.filter;
  if (DECK_VIEWS.includes(saved.view)) deckChoice.view = saved.view;
} catch { /* a browser without storage: the choice is for this page only */ }
let deckTags = [];

function chooseDeck(part, value) {
  deckChoice = { ...deckChoice, [part]: value };
  try {
    localStorage.setItem(DECK_KEY, JSON.stringify(deckChoice));
  } catch { /* see above */ }
  showDeck();
}
for (const button of $("deck-filter").querySelectorAll("button")) {
  button.addEventListener("click", () => chooseDeck("filter", button.dataset.filter));
}
for (const button of $("deck-view").querySelectorAll("button")) {
  button.addEventListener("click", () => chooseDeck("view", button.dataset.view));
}

function deckTag(tag) {
  const press = () => tag.present ? removeTag() : placeTag(tag.uid);
  const present = tag.present ? h("span", { class: "badge badge-ok deck-badge" }, t("common.present")) : "";
  if (deckChoice.view === "list") {
    return h("button", { class: `deck-row${tag.present ? " present" : ""}`, "aria-pressed": tag.present,
      disabled: isLibrary(), onclick: press },
    h("div", { class: "thumb" }, tagImage(tag)),
    h("div", { class: "deck-row-name" }, tag.name || tag.uid),
    h("div", { class: "muted small deck-row-target" }, targetText(tag) || t("common.not_linked")),
    h("div", { class: "muted small deck-row-progress" }, tagProgress(tag)),
    present);
  }
  return h("button", { class: `deck-card${tag.present ? " present" : ""}`, "aria-pressed": tag.present,
    disabled: isLibrary(),  // a tag is placed on a box
    onclick: press },
  h("div", { class: "cover" }, tagImage(tag)),
  h("div", { class: "deck-name" }, tag.name || tag.uid),
  h("div", { class: "muted small" }, targetText(tag) || t("common.not_linked")),
  present);
}

function showDeck() {
  for (const [id, part, attribute] of [["deck-filter", "filter", "filter"], ["deck-view", "view", "view"]]) {
    for (const button of $(id).querySelectorAll("button")) {
      button.setAttribute("aria-pressed", button.dataset[attribute] === deckChoice[part]);
    }
  }
  const shown = deckTags.filter((tag) => deckChoice.filter === "all"
    || tag[`${deckChoice.filter}_id`]);
  const deck = $("deck");
  deck.classList.toggle("deck-list", deckChoice.view === "list");
  deck.replaceChildren(...shown.map(deckTag));
  if (!shown.length) {
    deck.append(h("p", { class: "muted" }, deckTags.length ? t("tags.none.filtered")
      : isLibrary() ? t("tags.none.library") : t("tags.none.box")));
  }
}

async function loadDeck() {
  const tags = await run(() => api("GET", "/api/tags"));
  if (!tags) return;
  if (!albumsCache.length && !radiosCache.length && !spotifyCache.length) await loadTargets();
  deckTags = tags;
  showDeck();
}

async function placeTag(uid) {
  await run(() => api("POST", "/api/rfid", { uid }));
  setTimeout(() => { refreshStatus(); loadDeck(); }, 400);
}

async function removeTag() {
  await run(() => api("DELETE", "/api/rfid"));
  setTimeout(() => { refreshStatus(); loadDeck(); }, 400);
}

$("rfid-remove").addEventListener("click", removeTag);
$("rfid-form").addEventListener("submit", (event) => {
  event.preventDefault();
  placeTag(event.target.uid.value.trim());
  event.target.reset();
});

// ---------------------------------------------------------------------------------------------------------------
// the phone as reader of the library (docs/developer/library.md, section 5.1), in the tab of the tags: the browser
// reads the uid of a tag held to the phone (Web NFC, Chrome on Android) and the library adds the tag if it does
// not know it. The browser reports the touch only - nothing tells when the tag is taken away. Nothing is ever
// written to a tag.

const SCAN_ERRORS = ["NotSupportedError", "NotReadableError"];  // the errors of a scan that have a text of their own
// on a phone the button is always there: where the browser cannot read tags, a tap says why
const ON_A_PHONE = /Android|iPhone|iPad/.test(navigator.userAgent);
let scanStop = null;    // ends the running scan
let scannedUid = null;  // the tag that is shown

// why this browser cannot read tags - null if it can
function scanObstacle() {
  if ("NDEFReader" in window || APP) return null;
  const agent = navigator.userAgent;
  if (!/Android/.test(agent)) return t("scan.obstacle.ios");
  const chrome = /Chrome\/(\d+)/.exec(agent);
  if (!chrome || /Firefox|SamsungBrowser|EdgA|OPR|; wv\)/.test(agent)) {
    return t("scan.obstacle.browser");
  }
  if (!window.isSecureContext) return t("scan.obstacle.https");
  return t("scan.obstacle.chrome", { version: chrome[1] });
}

// Chrome did not let the page use NFC: what to do about it. The browser tells whether the page is blocked - by an
// answer to its question or for all pages - or whether its question was only closed
async function scanRefused() {
  let state = "denied";
  try {
    state = (await navigator.permissions.query({ name: "nfc" })).state;
  } catch { /* a browser that does not tell */ }
  return state === "prompt" ? t("scan.refused.closed") : t("scan.refused.blocked");
}

async function startScan() {
  const obstacle = scanObstacle();
  if (obstacle) return toast(obstacle, true);
  const stop = new AbortController();
  $("scan-refused").hidden = true;
  if (APP) {
    // the app reads the tag and gives the page the uid, as NDEFReader gives the serial number
    APP.onmessage = (event) => scanned(event.data);
    APP.postMessage("scan-start");
    stop.signal.addEventListener("abort", () => APP.postMessage("scan-stop"));
    scanStop = stop;
    forgetScanned();
    $("scan").hidden = false;
    $("scan-toggle").textContent = t("scan.stop");
    return;
  }
  try {
    const reader = new NDEFReader();
    reader.addEventListener("reading", (event) => scanned(event.serialNumber));
    reader.addEventListener("readingerror", () => toast(t("scan.unreadable"), true));
    await reader.scan({ signal: stop.signal });
  } catch (e) {
    if (e.name === "NotAllowedError") {  // stays on the page: the way out is too long for a message that fades
      $("scan-refused").textContent = await scanRefused();
      $("scan-refused").hidden = false;
    } else {
      toast(SCAN_ERRORS.includes(e.name) ? tOf("scan.error", e.name) : t("scan.failed", { message: e.message }), true);
    }
    return;
  }
  scanStop = stop;
  forgetScanned();
  $("scan").hidden = false;
  $("scan-toggle").textContent = t("scan.stop");
}

function stopScan() {
  if (!scanStop) return;
  scanStop.abort();
  scanStop = null;
  $("scan").hidden = true;
  $("scan-toggle").textContent = t("scan.start");
}

// the browser reports the uid as "04:1a:2b:3c:4d:5e:6f", the reader of a box in upper case
async function scanned(serialNumber) {
  if (!serialNumber) return toast(t("scan.unreadable"), true);
  const uid = serialNumber.toUpperCase();
  if (uid === scannedUid) return;  // some phones report a tag again and again while it stays
  const answer = await run(() => request("PUT", `/api/tags/${encodeURIComponent(uid)}`));
  if (!answer || !scanStop) return;
  scannedUid = uid;
  await loadTags();  // the tag in the table; also the albums and stations, for the texts below
  showScanned(answer.data, answer.status === 201);
}

function forgetScanned() {
  scannedUid = null;
  $("scan-result").replaceChildren();
}

function showScanned(tag, isNew) {
  const isAdmin = session.role === "admin";
  $("scan-result").replaceChildren(h("div", { class: "scan-tag" },
    h("div", { class: "thumb" }, tagImage(tag)),
    h("div", { class: "scan-fields" },
      h("div", {}, h("code", {}, tag.uid), isNew ? h("span", { class: "badge badge-ok small" }, t("scan.new")) : ""),
      isAdmin ? tagNameInput(tag, tagsChanged) : h("strong", {}, tag.name || t("common.unnamed")),
      isAdmin ? tagTargetSelect(tag, tagsChanged)
        : h("span", { class: "muted" }, targetText(tag) || t("common.not_linked")))));
}

// the tag that is shown, after its name or link was changed on this page
async function refreshScanned() {
  if (!scannedUid || !scanStop) return;
  const tag = await run(() => api("GET", `/api/tags/${encodeURIComponent(scannedUid)}`));
  if (tag && scanStop) showScanned(tag, false);
}

// a tag was changed on this page: the table, and the tag that is shown
async function tagsChanged() {
  await loadTags();
  await refreshScanned();
}

$("scan-toggle").addEventListener("click", () => scanStop ? stopScan() : startScan());

// ---------------------------------------------------------------------------------------------------------------
// log

let logLastId = 0;
let logBoxes = [];  // the page of the library: the boxes whose log can be chosen
let logRecords = [];  // ... and what is shown of the chosen one

async function loadLog(reset) {
  if (isLibrary() && $("log-source").value) return reset ? loadBoxLog() : undefined;  // no live view of a box
  $("log-box").hidden = $("log-startup").hidden = true;
  $("log-follow-label").hidden = false;
  const list = $("log-list");
  if (reset) {
    logLastId = 0;
    list.replaceChildren();
  }
  const params = new URLSearchParams({ after: logLastId, level: $("log-level").value, limit: 1000 });
  const text = $("log-text").value.trim();
  if (text) params.set("text", text);
  const result = await run(() => api("GET", `/api/logs?${params}`));
  if (!result) return;
  logLastId = result.last_id;
  if (!result.entries.length) {
    if (reset) list.append(h("p", { class: "muted" }, t("log.none")));
    return;
  }
  if (list.firstChild && list.firstChild.tagName === "P") list.replaceChildren();
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
  list.append(...result.entries.map((e) => h("div", { class: `log-entry log-${e.level.toLowerCase()}` },
    h("span", { class: "log-time" }, new Date(e.time * 1000).toLocaleTimeString()),
    h("span", { class: "log-level" }, e.level),
    h("span", { class: "log-logger" }, e.logger.split(".").slice(-2).join(".")),
    h("span", { class: "log-message" }, e.message))));
  while (list.children.length > 3000) list.firstChild.remove();
  if (atBottom || reset) list.scrollTop = list.scrollHeight;
}

// what a box delivered of its log (docs/developer/library.md, section 4.22) - no live view: it arrives in batches

async function logSources() {
  const overview = await run(() => api("GET", "/api/library/overview"));
  if (!overview) return;
  logBoxes = overview.boxes.filter((box) => !box.removed);
  const chosen = $("log-source").value;
  $("log-source").replaceChildren(h("option", { value: "" }, t("log.source.library")),
    ...logBoxes.map((box) => h("option", { value: box.id }, box.startup ? `${box.name} – ${t("log.cannot_start")}` : box.name)));
  $("log-source").value = logBoxes.some((box) => box.id === chosen) ? chosen : "";
}

function logFilters() {
  const params = new URLSearchParams({ box: $("log-source").value, level: $("log-level").value });
  for (const [key, id] of [["component", "log-component"], ["version", "log-version"], ["code", "log-code"],
    ["text", "log-text"]]) {
    if ($(id).value.trim()) params.set(key, $(id).value.trim());
  }
  if ($("log-since").value) params.set("since", new Date($("log-since").value).toISOString().replace(/\.\d+Z$/, "Z"));
  return params;
}

function fillChoice(id, values, everything) {
  const chosen = $(id).value;
  $(id).replaceChildren(h("option", { value: "" }, everything), ...values.map((value) => h("option", { value }, value)));
  $(id).value = values.includes(chosen) ? chosen : "";
}

async function loadBoxLog() {
  $("log-box").hidden = false;
  $("log-follow-label").hidden = true;
  const box = logBoxes.find((entry) => entry.id === $("log-source").value);
  const startup = box && box.startup;
  $("log-startup").hidden = !startup;
  if (startup) {
    $("log-startup-text").textContent = t("log.startup", { since: when(startup.since || startup.received),
      n: startup.failures, version: startup.version || "?", slot: startup.slot || "?" })
      + (startup.recovery ? ` ${t("log.recovery", { problem: startup.recovery })}` : "");
    $("log-startup-error").textContent = startup.error || "";
  }
  const params = logFilters();
  params.set("limit", "1000");
  const result = await run(() => api("GET", `/api/library/diagnostics?${params}`));
  if (!result) return;
  fillChoice("log-component", result.choices.component, t("log.component.all"));
  fillChoice("log-version", result.choices.version, t("log.version.all"));
  fillChoice("log-code", result.choices.code, t("log.code.all"));
  logRecords = result.records;
  const list = $("log-list");
  list.replaceChildren(...(logRecords.length ? logRecords.map((record) => h("div",
    { class: `log-entry log-${record.level.toLowerCase()}`, title: `${record.code} – ${record.version}` },
    h("span", { class: "log-time" }, new Date(record.time).toLocaleString()),
    h("span", { class: "log-level" }, record.level),
    h("span", { class: "log-logger" }, record.component),
    h("span", { class: "log-message" }, record.message + (record.repeated ? ` (×${record.repeated})` : ""),
      record.params ? h("span", { class: "muted" }, ` ${JSON.stringify(record.params)}`) : "",
      record.traceback ? h("details", {}, h("summary", {}, record.exception || t("log.traceback")),
        h("pre", {}, record.traceback)) : ""))) : [h("p", { class: "muted" }, t("log.none"))]));
  list.scrollTop = list.scrollHeight;
}

const logText = (records) => records.map((record) => [record.time, record.level, record.component, record.version,
  record.code, record.message + (record.traceback ? `\n${record.traceback}` : "")].join("\t")).join("\n");

$("log-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logText(logRecords));
    toast(t("log.copied", { n: logRecords.length }));
  } catch {
    window.getSelection().selectAllChildren($("log-list"));  // copied by hand then
  }
});
$("log-export").addEventListener("click", async () => {
  const response = await fetch(`/api/library/diagnostics/export?${logFilters()}`,
    { credentials: "same-origin", headers: requestHeaders() });
  if (!response.ok) return toast(t("log.export_failed"), true);
  const link = h("a", { href: URL.createObjectURL(await response.blob()),
    download: `diagnostics-${$("log-source").value}.jsonl` });
  link.click();
  URL.revokeObjectURL(link.href);
});
for (const id of ["log-source", "log-component", "log-version", "log-code", "log-since"]) {
  $(id).addEventListener("change", () => loadLog(true));
}

$("log-level").addEventListener("change", () => loadLog(true));
let logFilterTimer = null;
$("log-text").addEventListener("input", () => {
  clearTimeout(logFilterTimer);
  logFilterTimer = setTimeout(() => loadLog(true), 300);
});
$("tags-unmapped").addEventListener("change", loadTags);

// ---------------------------------------------------------------------------------------------------------------
// albums

// what the list is sorted by: the value of an album (null: at the end, in both directions). Albums with the same value
// stay in the order of their titles. A click on a column heading sorts by it, a second click turns the order round.
const collator = new Intl.Collator(undefined, { numeric: true });
const ALBUM_SORTS = {
  title: (a) => a.title,
  tracks: (a) => a.track_count,
  length: (a) => a.length,
  tag: (a) => a.tags.map((tag) => tag.name).filter(Boolean).sort(collator.compare)[0] ?? null,
  created: (a) => Date.parse(a.created) || null,
  folder: (a) => a.folder,
};
const ALBUMS_DESCENDING_FIRST = ["created", "tracks"];  // the first click on these shows the highest value first
let albumsShown = [];
let albumsSort = { key: "title", descending: false };

function sortedAlbums() {
  const value = ALBUM_SORTS[albumsSort.key];
  const direction = albumsSort.descending ? -1 : 1;
  return albumsShown.map((album) => [value(album), album]).sort(([x, a], [y, b]) => {
    if (x === null || y === null) return (x === null) - (y === null) || collator.compare(a.title, b.title);
    const order = typeof x === "number" ? x - y : collator.compare(x, y);
    return direction * order || (albumsSort.key === "created" ? 0 : collator.compare(a.title, b.title));
  }).map(([, album]) => album);
}

function showAlbums() {
  for (const head of $("albums-head").querySelectorAll("th[data-sort]")) {
    if (head.dataset.sort === albumsSort.key) head.setAttribute("aria-sort", albumsSort.descending ? "descending" : "ascending");
    else head.removeAttribute("aria-sort");
  }
  const list = $("albums-list");
  list.replaceChildren(...sortedAlbums().map((album) => h("tr", {},
    h("td", { class: "thumb-cell" }, h("div", { class: "thumb" }, coverElement(album.id))),
    h("td", {}, h("button", { class: "link album-open", onclick: () => openAlbum(album.id) }, album.title),
      album.broken ? h("span", { class: "badge badge-error small", title: t("albums.broken.title") }, t("albums.broken")) : ""),
    h("td", {}, String(album.track_count)),
    h("td", {}, album.length ? duration(Math.round(album.length)) : "–"),
    h("td", { class: "small" }, album.tags.length ? album.tags.map((tag) => tag.name || tag.uid).join(", ") : t("common.no_tag")),
    h("td", { class: "small" }, new Date(album.created).toLocaleDateString()),
    h("td", { class: "small" }, h("code", {}, album.folder)))));
  if (!albumsShown.length) list.append(h("tr", {}, h("td", { colSpan: 7, class: "muted" }, t("albums.none"))));
}

async function loadAlbums() {
  const unmapped = $("albums-unmapped").checked;
  const albums = await run(() => api("GET", `/api/albums${unmapped ? "?unmapped=1" : ""}`));
  if (!albums) return;
  albumsShown = albums;
  showAlbums();
}
$("albums-unmapped").addEventListener("change", loadAlbums);

for (const head of $("albums-head").querySelectorAll("th[data-sort]")) {
  const sort = () => {
    const key = head.dataset.sort;
    albumsSort = { key, descending: albumsSort.key === key ? !albumsSort.descending : ALBUMS_DESCENDING_FIRST.includes(key) };
    showAlbums();
  };
  head.tabIndex = 0;
  head.addEventListener("click", sort);
  head.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    sort();
  });
}

let dialogAlbum = null;
async function openAlbum(id) {
  const album = await run(() => api("GET", `/api/albums/${encodeURIComponent(id)}`));
  if (!album) return;
  dialogAlbum = album;
  $("dlg-cover").replaceChildren(coverElement(album.id));
  $("dlg-title").replaceChildren(session.role === "admin" ? albumTitleInput(album) : album.title);
  $("dlg-meta").textContent = [album.artist, t("album.folder", { folder: album.folder }),
    t("albums.tracks", { n: album.track_count }), formatTime(album.length),
    t("album.added", { date: new Date(album.created).toLocaleDateString() })].filter(Boolean).join(" · ");
  $("dlg-tags").replaceChildren(...tagChips(album.tags, () => { openAlbum(album.id); loadAlbums(); }));
  $("dlg-tracks").replaceChildren(...album.tracks.map((track) => h("li", {},
    h("span", { class: "track-name" }, track.title || track.filename,
      track.missing ? h("span", { class: "danger-text" }, ` ${t("album.track_missing")}`) : ""),
    h("span", { class: "muted small" }, formatTime(track.length)))));
  $("dlg-delete-files").checked = false;
  $("dlg-problems").hidden = !album.problems.length;
  $("dlg-problem-list").replaceChildren(...album.problems.slice(0, 8).map((p) => h("li", {}, p)),
    album.problems.length > 8 ? h("li", {}, t("album.more_problems", { n: album.problems.length - 8 })) : "");
  // library: files that boxes found damaged (what the library found itself is among the problems above)
  const reports = (album.broken || []).flatMap((found) => found.boxes.map((report) => h("li", { title: report.error },
    t("album.broken.report", { file: found.file, box: report.name, date: new Date(report.when).toLocaleDateString() }))));
  $("dlg-broken").hidden = !reports.length;
  $("dlg-broken-list").replaceChildren(...reports);
  if (!$("album-dialog").open) $("album-dialog").showModal();
}

// input that changes the title of an album (admins)
function albumTitleInput(album) {
  const input = h("input", { value: album.title, class: "inline-input title-input", maxLength: 200, "aria-label": t("common.title") });
  input.addEventListener("change", async () => {
    const saved = await run(() => api("PATCH", `/api/albums/${encodeURIComponent(album.id)}`, { title: input.value }),
      t("album.title_saved"));
    if (saved) {
      album.title = saved.title;
      loadTargets();
      if (currentTab === "albums") loadAlbums();
    }
    input.value = album.title;
  });
  return input;
}

$("dlg-cover-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file || !(await uploadCover(dialogAlbum.id, file))) return;
  toast(t("cover.saved"));
  $("dlg-cover").replaceChildren(coverElement(dialogAlbum.id));
  if (currentTab === "albums") loadAlbums();
});

$("dlg-playlist").addEventListener("click", async () => {
  if (!confirm(t("album.playlist.confirm"))) return;
  const album = await run(() => api("POST", `/api/albums/${encodeURIComponent(dialogAlbum.id)}/playlist`), t("album.playlist.done"));
  if (album) openAlbum(album.id);
});

$("dlg-broken-forget").addEventListener("click", async () => {
  const album = await run(() => api("DELETE", `/api/library/albums/${encodeURIComponent(dialogAlbum.id)}/broken`),
    t("album.broken.forgotten"));
  if (!album) return;
  openAlbum(album.id);
  if (currentTab === "albums") loadAlbums();
});

$("dlg-delete").addEventListener("click", async () => {
  const album = dialogAlbum;
  const deleteFiles = $("dlg-delete-files").checked;
  if (!confirm(t(deleteFiles ? "album.delete.confirm_files" : "album.delete.confirm", { title: album.title }))) return;
  const result = await run(() => api("DELETE",
    `/api/albums/${encodeURIComponent(album.id)}${deleteFiles ? "?delete_files=1" : ""}`), t("album.removed"));
  if (result !== undefined) {
    $("album-dialog").close();
    loadAlbums();
  }
});

$("albums-scan").addEventListener("click", async () => {
  const found = await run(() => api("GET", "/api/albums/new"));
  if (!found) return;
  if (!found.length) return toast(t("albums.scan.none"));
  const lines = found.map((a) => a.title === a.folder ? a.folder : t("albums.scan.line", { folder: a.folder, title: a.title }));
  if (!confirm(t("albums.scan.confirm", { n: found.length, folders: lines.join("\n") }))) return;
  const result = await run(() => api("POST", "/api/albums/new",
    { titles: Object.fromEntries(found.map((a) => [a.folder, a.title])) }));
  if (!result) return;
  toast(t(result.skipped.length ? "albums.scan.added_skipped" : "albums.scan.added",
    { n: result.added.length, skipped: result.skipped.length }), result.skipped.length > 0);
  loadAlbums();
});

$("album-add").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const album = await run(() => api("POST", "/api/albums", { folder: form.folder.value, title: form.title.value }),
    t("albums.add.done"));
  if (album) {
    form.reset();
    loadAlbums();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// radio stations

async function loadRadios() {
  const radios = await run(() => api("GET", "/api/radios"));
  if (!radios) return;
  radiosCache = radios;
  const isAdmin = session.role === "admin";
  const tagNames = (radio) => radio.tags.length ? radio.tags.map((tag) => tag.name || tag.uid).join(", ") : t("common.no_tag");
  $("radios-body").replaceChildren(...radios.map((radio) => {
    const tagsCell = h("td", {}, h("div", { class: "tag-chips" }, ...tagChips(radio.tags, loadRadios)));
    if (!isAdmin) {
      return h("tr", {}, h("td", {}, h("div", { class: "thumb" }, radioCoverElement(radio))),
        h("td", {}, radio.name), h("td", { class: "radio-url small" }, radio.url), tagsCell, h("td", {}));
    }
    const file = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", hidden: true });
    file.addEventListener("change", async () => {
      if (!file.files[0] || !(await sendCover(`/api/radios/${encodeURIComponent(radio.id)}`, file.files[0]))) return;
      toast(t("cover.saved"));
      loadRadios();
    });
    const cover = h("label", { class: "button thumb", title: t("cover.change") }, radioCoverElement(radio), file);
    const name = h("input", { value: radio.name, class: "inline-input", maxLength: 100, "aria-label": t("common.name") });
    const url = h("input", { type: "url", value: radio.url, class: "inline-input", maxLength: 2000, "aria-label": t("radio.column.url") });
    const save = h("button", { class: "primary", disabled: true, onclick: async () => {
      const result = await run(() => api("PATCH", `/api/radios/${encodeURIComponent(radio.id)}`,
        { name: name.value, url: url.value }), t("radio.saved"));
      if (result) loadRadios();
    } }, t("common.save"));
    const test = h("button", { class: "box-only", onclick: () => testRadio(url.value, name.value) }, t("radio.test"));
    const changed = () => { save.disabled = name.value === radio.name && url.value === radio.url; };
    name.addEventListener("input", changed);
    url.addEventListener("input", changed);
    const remove = h("button", { class: "link danger-text", onclick: async () => {
      if (radio.tags.length) {
        return toast(t("radio.remove.linked", { name: radio.name, tags: tagNames(radio) }), true);
      }
      if (!confirm(t("radio.remove.confirm", { name: radio.name }))) return;
      const result = await run(() => api("DELETE", `/api/radios/${encodeURIComponent(radio.id)}`), t("radio.removed"));
      if (result !== undefined) loadRadios();
    } }, t("common.delete"));
    return h("tr", {}, h("td", {}, cover), h("td", {}, name), h("td", {}, url), tagsCell,
      h("td", { class: "radio-actions" }, test, save, remove));
  }));
  if (!radios.length) $("radios-body").append(h("tr", {}, h("td", { colSpan: 5, class: "muted" }, t("radio.none"))));
}

// plays the entered url for a minute (only while no tag is on the box) - the state comes with the status events
async function testRadio(url, name) {
  const result = await run(() => api("POST", "/api/radio-test", { url, name: name || undefined }));
  if (result) setTimeout(refreshStatus, 300);
}

$("radio-new-test").addEventListener("click", () => {
  const form = $("radio-new");
  testRadio(form.url.value, form.name.value);
});
$("radio-test-stop").addEventListener("click", async () => {
  await run(() => api("DELETE", "/api/radio-test"));
  setTimeout(refreshStatus, 300);
});

$("radio-new").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const radio = await run(() => api("POST", "/api/radios", { name: form.name.value, url: form.url.value }));
  if (radio) {
    toast(t("radio.created", { name: radio.name }));
    form.reset();
    loadRadios();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// entries of Spotify

async function loadSpotify() {
  const entries = await run(() => api("GET", "/api/spotify"));
  if (!entries) return;
  spotifyCache = entries;
  const isAdmin = session.role === "admin";
  const tagNames = (entry) => entry.tags.length ? entry.tags.map((tag) => tag.name || tag.uid).join(", ") : t("common.no_tag");
  const kind = (entry) => tOf("spotify.kind", entry.kind);
  const linkElement = (entry) => h("a", { href: entry.link, target: "_blank", rel: "noopener", class: "small" }, entry.link);
  $("spotify-body").replaceChildren(...entries.map((entry) => {
    const tagsCell = h("td", {}, h("div", { class: "tag-chips" }, ...tagChips(entry.tags, loadSpotify)));
    if (!isAdmin) {
      return h("tr", {}, h("td", {}, h("div", { class: "thumb" }, spotifyCoverElement(entry))),
        h("td", {}, entry.name), h("td", {}, kind(entry)), h("td", { class: "radio-url" }, linkElement(entry)), tagsCell, h("td", {}));
    }
    const path = `/api/spotify/${encodeURIComponent(entry.id)}`;
    const file = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", hidden: true });
    file.addEventListener("change", async () => {
      if (!file.files[0] || !(await sendCover(path, file.files[0]))) return;
      toast(t("cover.saved"));
      loadSpotify();
    });
    const cover = h("label", { class: "button thumb", title: t("cover.change") }, spotifyCoverElement(entry), file);
    const name = h("input", { value: entry.name, class: "inline-input", maxLength: 100, "aria-label": t("common.name") });
    const link = h("input", { value: entry.link, class: "inline-input", maxLength: 2000, "aria-label": t("spotify.column.link") });
    const save = h("button", { class: "primary", disabled: true, onclick: async () => {
      const result = await run(() => api("PATCH", path, { name: name.value, link: link.value }), t("spotify.saved"));
      if (result) loadSpotify();
    } }, t("common.save"));
    const changed = () => { save.disabled = name.value === entry.name && link.value === entry.link; };
    name.addEventListener("input", changed);
    link.addEventListener("input", changed);
    const remove = h("button", { class: "link danger-text", onclick: async () => {
      if (entry.tags.length) {
        return toast(t("spotify.remove.linked", { name: entry.name, tags: tagNames(entry) }), true);
      }
      if (!confirm(t("spotify.remove.confirm", { name: entry.name }))) return;
      const result = await run(() => api("DELETE", path), t("spotify.removed"));
      if (result !== undefined) loadSpotify();
    } }, t("common.delete"));
    const test = h("button", { class: "box-only", onclick: () => testSpotify({ id: entry.id }) }, t("radio.test"));
    return h("tr", {}, h("td", {}, cover), h("td", {}, name), h("td", {}, kind(entry)), h("td", {}, link), tagsCell,
      h("td", { class: "radio-actions" }, test, save, remove));
  }));
  if (!entries.length) $("spotify-body").append(h("tr", {}, h("td", { colSpan: 6, class: "muted" }, t("spotify.none"))));
  loadSpotifyAccount();
  loadSpotifyApp();
}

// the library: the client id of the app for phones at Spotify, which every phone gets (app.md, section 3)
async function loadSpotifyApp() {
  if (!isLibrary() || !session || session.role !== "admin") return;
  const overview = await run(() => api("GET", "/api/library/overview"));
  if (overview) $("spotify-app-form").client.value = (overview.app || {}).spotify_client || "";
}

$("spotify-app-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await run(() => api("PUT", "/api/library/spotify-client", { client: event.target.client.value.trim() }), t("common.saved"));
  loadSpotifyApp();
});

// the cards "Account" and "Player" (a box, admins): while the box waits to be chosen in the Spotify app or fetches
// the player, the page asks every two seconds
let spotifyTimer = null;

async function loadSpotifyAccount() {
  if (isLibrary() || !session || session.role !== "admin") return;
  const [account, player] = await Promise.all([run(() => api("GET", "/api/spotify-account")),
    run(() => api("GET", "/api/spotify-player"))]);
  if (!account || !player) return;
  const since = account.connected_since ? new Date(account.connected_since).toLocaleString() : "";
  const login = account.connecting ? t("spotify.account.state.connecting")
    : account.connected ? t("spotify.account.state.connected", { since })
    : account.connect_result && account.connect_result !== "connected" ? tOf("spotify.account.result", account.connect_result)
    : t("spotify.account.state.none");
  $("spotify-account-state").textContent = [account.key ? t("spotify.account.key_present") : t("spotify.account.key_missing"),
    login].join(" ");
  $("spotify-connect").disabled = !account.key || account.connecting || player.state !== "ready";
  $("spotify-disconnect").disabled = !account.connected || account.connecting;
  $("spotify-speaker").checked = account.speaker;
  const day = (stamp) => stamp ? new Date(stamp).toLocaleDateString() : "–";
  $("spotify-player-state").textContent = [tOf("spotify.player.state", player.state) + (player.error ? ` (${player.error})` : ""),
    player.built ? t("spotify.player.built", { built: day(player.built), last_day: day(player.last_day), renew_from: day(player.renew_from) }) : ""]
    .filter(Boolean).join(" ");
  $("spotify-player-asked").textContent = player.asked
    ? t("spotify.player.asked", { time: new Date(player.asked).toLocaleString(), result: tOf("spotify.player.result", player.result) })
    : t("spotify.player.never");
  $("spotify-player-missing").hidden = !player.missing.length;
  $("spotify-player-missing").textContent = t("spotify.player.missing", { packages: player.missing.join(", ") });
  $("spotify-update").disabled = !account.key || player.state === "fetching";
  clearTimeout(spotifyTimer);
  spotifyTimer = (account.connecting || player.state === "fetching") && currentTab === "spotify"
    ? setTimeout(loadSpotifyAccount, 2000) : null;
}

$("spotify-key").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (await run(() => api("PUT", "/api/spotify-account/key", { key: form.key.value }), t("spotify.account.key_saved"))) {
    form.reset();
    loadSpotifyAccount();
  }
});
$("spotify-connect").addEventListener("click", async () => {
  if (await run(() => api("POST", "/api/spotify-account"), t("spotify.account.choose"))) loadSpotifyAccount();
});
$("spotify-disconnect").addEventListener("click", async () => {
  if (!confirm(t("spotify.account.disconnect.confirm"))) return;
  if (await run(() => api("DELETE", "/api/spotify-account"))) loadSpotifyAccount();
});
$("spotify-speaker").addEventListener("change", async (event) => {
  await run(() => api("PUT", "/api/spotify-account/speaker", { enabled: event.target.checked }));
  loadSpotifyAccount();
});
$("spotify-update").addEventListener("click", async () => {
  if (await run(() => api("POST", "/api/spotify-player"), t("spotify.player.updating"))) loadSpotifyAccount();
});

// plays an entry for a minute (only while no tag is on the box) - the state comes with the status events
async function testSpotify(what) {
  const result = await run(() => api("POST", "/api/spotify-test", what));
  if (result) setTimeout(refreshStatus, 300);
}

$("spotify-new-test").addEventListener("click", () => testSpotify({ link: $("spotify-new").link.value }));
$("spotify-test-stop").addEventListener("click", async () => {
  await run(() => api("DELETE", "/api/spotify-test"));
  setTimeout(refreshStatus, 300);
});

// a pasted link: Spotify is asked for the name, which fills the field if nobody typed one
$("spotify-new").link.addEventListener("change", async () => {
  const form = $("spotify-new");
  if (!form.link.value.trim() || form.name.value.trim()) return;
  const found = await run(() => api("POST", "/api/spotify-lookup", { link: form.link.value }));
  if (found && found.name && !form.name.value.trim()) form.name.value = found.name;
});

$("spotify-new").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const entry = await run(() => api("POST", "/api/spotify", { link: form.link.value, name: form.name.value || undefined }));
  if (entry) {
    toast(t("spotify.created", { name: entry.name }));
    form.reset();
    loadSpotify();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// upload

let upload = null;         // {id, folder}
let uploadQueue = [];      // [{file, name, state, progress, info, li}]
let uploading = false;

async function loadUpload() {
  loadUploadTags();
  const pending = (await run(() => api("GET", "/api/uploads"))) || [];
  const others = pending.filter((p) => !upload || p.id !== upload.id);
  $("pending-uploads").hidden = others.length === 0;
  $("pending-list").replaceChildren(...others.map((p) => h("li", {},
    h("span", { class: "pending-name" }, p.folder),
    h("span", { class: "muted small" }, t("upload.files", { n: p.files.length })),
    h("button", { disabled: upload !== null, title: upload ? t("upload.pending.busy") : "",
      onclick: () => resumeUpload(p) }, t("upload.pending.resume")),
    h("button", { class: "danger", onclick: async () => {
      if (!confirm(t("upload.pending.discard.confirm", { folder: p.folder }))) return;
      await run(() => api("DELETE", `/api/uploads/${p.id}`));
      loadUpload();
    } }, t("upload.pending.discard")))));
}

function resumeUpload(pending) {
  upload = { id: pending.id, folder: pending.folder };
  $("upload-name").value = pending.title || pending.folder;
  $("upload-name").disabled = true;
  const position = Object.fromEntries(pending.playlist.map((name, i) => [name, i]));
  uploadQueue = pending.files.map((info) => ({ file: null, name: info.filename, state: "ok", progress: 1, info }))
    .sort((a, b) => position[a.name] - position[b.name]);
  renderUploadList();
  loadUpload();
}

async function loadUploadTags() {
  const [tags, status] = await Promise.all([run(() => api("GET", "/api/tags?unmapped=1")), run(() => api("GET", "/api/status"))]);
  const select = $("upload-tag");
  const selected = select.value;
  const options = [h("option", { value: "" }, t("upload.link_later"))];
  if (status && status.tag) {
    options.push(h("option", { value: "current" }, t("upload.present_tag", { name: status.tag.name || status.tag.uid })));
  }
  for (const tag of tags || []) {
    options.push(h("option", { value: tag.uid }, `${tag.name || t("common.unnamed")} (${tag.uid})`));
  }
  select.replaceChildren(...options);
  select.value = selected;
}

function mp3Files(fileList) {
  return [...fileList].filter((f) => f.name.toLowerCase().endsWith(".mp3"));
}

async function addFiles(files) {
  files = mp3Files(files);
  if (!files.length) return toast(t("upload.no_mp3"), true);
  const nameInput = $("upload-name");
  if (!nameInput.value.trim()) {
    // folder upload: use the folder name as album name
    const path = files[0].webkitRelativePath || "";
    nameInput.value = path.includes("/") ? path.split("/")[0] : "";
  }
  if (!nameInput.value.trim()) {
    nameInput.focus();
    return toast(t("upload.title_required"), true);
  }
  if (!upload) {
    upload = await run(() => api("POST", "/api/uploads", { name: nameInput.value }));
    if (!upload) return;
    nameInput.value = upload.title;
    nameInput.disabled = true;
  }
  for (const file of files) {
    const existing = uploadQueue.find((entry) => entry.name === file.name);
    if (existing) uploadQueue.splice(uploadQueue.indexOf(existing), 1);
    uploadQueue.push({ file, name: file.name, state: "waiting", progress: 0, info: null });
  }
  renderUploadList();
  processQueue();
}

function sendFile(entry) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads/${upload.id}/files/${encodeURIComponent(entry.name)}`);
    uploadsRunning += 1;
    xhr.addEventListener("loadend", () => { uploadsRunning -= 1; noteInput(); });
    xhr.setRequestHeader("X-TinyJukebox-Request", "1");
    xhr.setRequestHeader("X-TinyJukebox-Idle", "0");
    xhr.setRequestHeader("Content-Type", "audio/mpeg");
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        entry.progress = e.loaded / e.total;
        renderUploadEntry(entry);
      }
    });
    xhr.addEventListener("load", () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* keep null */ }
      if (xhr.status === 201) {
        entry.state = "ok";
        entry.info = data;
        entry.name = data.filename;
      } else {
        entry.state = "failed";
        entry.error = errorText(data, xhr.status);
        if (xhr.status === 401) showLogin();
      }
      resolve();
    });
    xhr.addEventListener("error", () => {
      entry.state = "failed";
      entry.error = t("upload.connection_lost");
      resolve();
    });
    entry.state = "sending";
    xhr.send(entry.file);
  });
}

async function processQueue() {
  if (uploading) return;
  uploading = true;
  updateUploadButtons();
  let entry;
  while ((entry = uploadQueue.find((e) => e.state === "waiting"))) {
    await sendFile(entry);
    renderUploadEntry(entry);
  }
  uploading = false;
  if (!upload) return;  // aborted while the files were sent
  // order suggested by the server: track numbers, otherwise file names
  const state = await run(() => api("GET", `/api/uploads/${upload.id}`));
  if (state) {
    const position = Object.fromEntries(state.playlist.map((name, i) => [name, i]));
    uploadQueue.sort((a, b) => (position[a.name] ?? 1e9) - (position[b.name] ?? 1e9));
  }
  renderUploadList();
}

function renderUploadEntry(entry) {
  if (!entry.li) return;
  const info = entry.info;
  entry.li.querySelector(".upload-name").textContent = info && info.title ? `${info.title}  (${entry.name})` : entry.name;
  entry.li.querySelector(".upload-state").textContent = entry.state === "failed" ? entry.error
    : entry.state === "ok" ? formatTime(info && info.length) : entry.state === "sending"
      ? percent(Math.round(entry.progress * 100)) : t("upload.waiting");
  entry.li.querySelector(".progress-bar").style.width = `${Math.round((entry.state === "ok" ? 1 : entry.progress) * 100)}%`;
  entry.li.classList.toggle("upload-error", entry.state === "failed");
  entry.li.classList.toggle("upload-ok", entry.state === "ok");
}

function moveEntry(entry, delta) {
  const index = uploadQueue.indexOf(entry);
  const target = index + delta;
  if (target < 0 || target >= uploadQueue.length) return;
  uploadQueue.splice(index, 1);
  uploadQueue.splice(target, 0, entry);
  renderUploadList();
}

async function removeEntry(entry) {
  if (entry.state === "ok") {
    const ok = await run(() => api("DELETE", `/api/uploads/${upload.id}/files/${encodeURIComponent(entry.name)}`));
    if (ok === undefined) return;
  }
  uploadQueue.splice(uploadQueue.indexOf(entry), 1);
  renderUploadList();
}

function renderUploadList() {
  $("upload-list").replaceChildren(...uploadQueue.map((entry) => {
    entry.li = h("li", { class: "upload-entry" },
      h("div", { class: "upload-row" },
        h("span", { class: "upload-name" }),
        h("span", { class: "upload-state muted small" }),
        h("span", { class: "upload-actions" },
          h("button", { class: "link", title: t("upload.move_up"), "aria-label": t("upload.move_up"), onclick: () => moveEntry(entry, -1) }, "↑"),
          h("button", { class: "link", title: t("upload.move_down"), "aria-label": t("upload.move_down"), onclick: () => moveEntry(entry, 1) }, "↓"),
          h("button", { class: "link danger-text", title: t("upload.remove"), "aria-label": t("upload.remove"), onclick: () => removeEntry(entry) }, "✕"))),
      h("div", { class: "progress progress-thin" }, h("div", { class: "progress-bar" })));
    renderUploadEntry(entry);
    return entry.li;
  }));
  updateUploadButtons();
}

function updateUploadButtons() {
  const done = uploadQueue.filter((e) => e.state === "ok").length;
  $("upload-finish").disabled = uploading || done === 0 || uploadQueue.some((e) => e.state !== "ok");
  $("upload-abort").disabled = !upload;
  $("upload-message").textContent = uploadQueue.length ? t("upload.progress", { done, n: uploadQueue.length }) : "";
}

function resetUpload() {
  upload = null;
  uploadQueue = [];
  $("upload-name").disabled = false;
  $("upload-name").value = "";
  $("upload-cover").value = "";
  renderUploadList();
}

$("upload-files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
$("upload-folder").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

const dropzone = $("dropzone");
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragging"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
  addFiles(e.dataTransfer.files);
});

$("upload-finish").addEventListener("click", async () => {
  const tag = $("upload-tag").value;
  const result = await run(() => api("POST", `/api/uploads/${upload.id}/finish`, {
    order: uploadQueue.map((e) => e.name), tag_uid: tag || undefined,
  }));
  if (!result) return;
  const cover = $("upload-cover").files[0];
  if (cover) await uploadCover(result.album.id, cover);  // a refused image is reported; the album is there
  toast(t(result.tag ? "upload.created_linked" : "upload.created", { title: result.album.title }));
  resetUpload();
  loadUpload();
});

$("upload-abort").addEventListener("click", async () => {
  if (!confirm(t("upload.abort.confirm"))) return;
  await run(() => api("DELETE", `/api/uploads/${upload.id}`));
  resetUpload();
  loadUpload();
});

// ---------------------------------------------------------------------------------------------------------------
// the library as a box sees it (tab "System")

async function loadLibrary() {
  let info;
  try {
    info = await api("GET", "/api/box");
  } catch (e) {
    return;
  }
  $("lib-url").textContent = info.library || t("library.none");
  const state = !info.library ? "–"
    : !info.attached ? t("library.state.next_start")
    : info.blocked ? t("library.state.blocked")
    : info.connected ? t("library.state.connected") : t("library.state.unreachable",
      { reason: known("library.alone", info.alone_code) ? tOf("library.alone", info.alone_code) : info.alone || "…" });
  $("lib-state").textContent = state;
  $("lib-box").textContent = info.name ? `${info.name} (${info.box})` : info.box;
  $("lib-outbox").textContent = !info.attached ? "–"
    : t(info.unsettled.length ? "library.outbox_unsettled" : "library.outbox", { n: info.outbox, unsettled: info.unsettled.length });
  const form = $("lib-form");
  if (document.activeElement !== form.url) form.url.value = info.library || "";
  $("lib-rename").hidden = !info.attached;
  $("lib-rename").dataset.name = info.name || "";
  $("lib-unlink-row").hidden = !info.library && !info.attached;
}

$("lib-unlink").addEventListener("click", async () => {
  // asked anew: what waits may have changed since the card was drawn
  const info = await run(() => api("GET", "/api/box"));
  if (!info) return;
  const lines = [t("library.unlink.confirm")];
  if (info.outbox) lines.push(t("library.unlink.changes", { n: info.outbox }));
  if (info.absent) lines.push(t("library.unlink.absent", { n: info.absent }));
  if (info.outbox || info.absent) lines.push(t("library.unlink.wait"));
  if (!confirm(lines.join("\n\n"))) return;
  if (!await run(() => api("DELETE", "/api/box/library"), t("library.unlinked"))) return;
  loadLibrary();
  if (session.role === "admin") loadVersion();
});

$("lib-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = { url: form.url.value.trim() };
  if (form.bootstrap.value) body.bootstrap = form.bootstrap.value;
  const info = await run(() => api("PUT", "/api/box/library", body), t("library.address.saved"));
  form.bootstrap.value = "";
  if (info) loadLibrary();
});

// as on the page of the library: the name that is there can be changed, not a new one typed into an empty field
$("lib-rename").addEventListener("click", async (event) => {
  const name = prompt(t("boxes.rename.prompt"), event.target.dataset.name);
  if (!name) return;
  const info = await run(() => api("PUT", "/api/box/name", { name }), t("common.name_changed"));
  if (!info) return;
  loadLibrary();
  loadIdentity();  // the new name, and with it the colors of the box
});

// ---------------------------------------------------------------------------------------------------------------
// the version of the box software (tab "System", admins): everything goes through tinyjukebox-slot

let offered = null;  // the release that a box without a library has not installed yet

async function loadVersion() {
  let info;
  try {
    info = await api("GET", "/api/box/system");
  } catch (e) {
    return;
  }
  // where versions come from: the library as long as the box is linked to one (an older slot script does not say)
  const origin = info.origin || (info.library ? "library" : "none");
  // damaged or unknown data this version left out: the box works without it
  const left = info.degraded || [];
  $("ver-degraded").hidden = $("ver-degraded-label").hidden = !left.length;
  $("ver-degraded").firstElementChild.replaceChildren(...left.map((note) => h("li", {}, note)));
  $("ver-controls").hidden = !info.available;
  $("ver-help").textContent = info.available ? tOf("version.help", origin) : "";
  $("ver-source").textContent = origin === "library" ? t("version.source.library", { address: info.library })
    : origin === "releases" ? t("version.source.releases", { address: info.releases, n: info.wait })
    : t("version.source.none");
  const select = $("ver-mode-select");
  const offer = info.available ? info.offer : null;
  const asks = Boolean(offer) && info.mode === "manual";  // the box asks: nothing is downloaded before the answer
  offered = offer;
  select.hidden = !info.available;
  $("ver-offer").hidden = $("ver-offer-label").hidden = !offer;
  $("ver-offer-skip").hidden = $("version-banner").hidden = !asks;
  if (asks) $("version-banner-text").textContent = t("version.banner", { version: offer.version });
  if (!info.available) {
    $("ver-running").textContent = info.version;
    $("ver-mode").textContent = t("version.no_slots");
    return;
  }
  $("ver-mode").textContent = "";
  const modes = [...(info.script >= 2 ? ["off"] : []), "manual", "automatic"];  // "off" needs the slot script 2
  select.replaceChildren(...modes.map((mode) => h("option", { value: mode },
    mode === "manual" && origin === "releases" ? t("version.mode.ask") : tOf("version.mode", mode))));
  select.value = info.mode;
  if (offer) {
    // a release that a box without a library has not installed yet: it waits for its day, or for a person
    const values = { version: offer.version, size: formatSize(offer.size), n: info.wait,
      date: new Date(offer.published).toLocaleDateString(), due: offer.due ? new Date(offer.due).toLocaleDateString() : "" };
    $("ver-offer-text").textContent = asks ? t("version.offer", values)
      : t(offer.due ? "version.offer.waiting" : "version.offer.soon", values);
    $("ver-offer-install").textContent = t(asks ? "version.install" : "version.install_now");
    $("ver-offer-notes").hidden = !offer.notes;
    $("ver-offer-notes").querySelector("pre").textContent = offer.notes || "";
  }
  const slots = info.slots || {};
  const running = slots[info.active] || {};
  $("ver-running").textContent = t("version.running_slot",
    { version: info.version, slot: tOf("slot.name", info.active), state: tOf("slot.state", running.state) });
  $("ver-problem").textContent = info.problem || "";
  $("ver-problem").hidden = $("ver-problem-label").hidden = !info.problem;
  $("ver-slots").replaceChildren(...Object.entries(slots).map(([name, slot]) => {
    const state = name === "next" ? t("slot.installed") : tOf("slot.state", slot.state);
    return h("tr", { class: name === info.active ? "present" : "" },
      h("td", {}, tOf("slot.name", name)),
      h("td", {}, slot.version),
      h("td", {}, slot.state === "probation" ? t("slot.since", { state, since: when(slot.since), n: slot.starts }) : state));
  }));
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-slot-command]");
  if (!button) return;
  if (button.dataset.confirm && !confirm(button.dataset.confirm.replace(/\s+/g, " "))) return;
  const [command, ...args] = button.dataset.slotCommand.split(" ");
  button.disabled = true;
  await slotCommand(command, args);
  button.disabled = false;
});

async function slotCommand(command, args) {
  const result = await run(() => api("POST", `/api/box/system/${command}`, { arguments: args }));
  if (!result) return;
  $("ver-output").textContent = result.output || (result.status === 0 ? t("version.done") : t("error.status", { status: result.status }));
  $("ver-output").hidden = false;
  if (result.status !== 0) toast(t("version.failed"), true);
  loadVersion();
}

$("ver-mode-select").addEventListener("change", (event) => slotCommand("mode", [event.target.value]));

// a release of a box without a library (section "Version", and the banner of a box that asks)

async function installOffer() {
  if (!offered) return;
  // in the background, as every update: the version starts the next time the box is switched on
  $("version-banner").hidden = true;
  toast(t("version.installing", { version: offered.version }));
  for (const id of ["ver-offer-install", "version-banner-install"]) $(id).disabled = true;
  await slotCommand("update", []);
  for (const id of ["ver-offer-install", "version-banner-install"]) $(id).disabled = false;
}

async function skipOffer() {
  if (!offered) return;
  const version = offered.version;
  $("version-banner").hidden = true;
  await slotCommand("skip", [version]);
  toast(t("version.skipped", { version }));
}

$("ver-offer-install").addEventListener("click", installOffer);
$("version-banner-install").addEventListener("click", installOffer);
$("ver-offer-skip").addEventListener("click", skipOffer);
$("version-banner-skip").addEventListener("click", skipOffer);
$("version-banner-notes").addEventListener("click", () => {
  selectTab("system");
  $("ver-offer-notes").open = true;
  $("ver-offer").scrollIntoView({ block: "center" });
});

// ---------------------------------------------------------------------------------------------------------------
// the page of the library: boxes, versions of the box software

const when = (stamp) => stamp ? new Date(stamp).toLocaleString() : t("common.never");

async function loadBoxes() {
  const overview = await run(() => api("GET", "/api/library/overview"));
  if (!overview) return;
  const isAdmin = session.role === "admin";
  const active = overview.boxes.filter((box) => !box.removed);
  $("boxes-body").replaceChildren(...overview.boxes.map((box) => {
    const status = box.status || {};
    const byHand = box.interface < overview.interface && ["manual", "off"].includes(status.mode);
    const off = status.mode === "off";
    const fits = !box.fits ? "" : t(byHand ? "boxes.fits_manual" : "boxes.fits",
      { name: box.name, speaks: box.interface, serves: overview.interface });
    const phone = box.kind === "app";
    const left = status.degraded || [];
    const startup = box.startup ? t("boxes.startup", { since: when(box.startup.since || box.startup.received),
      n: box.startup.failures }) : "";
    const notes = [startup, fits, status.problem, left.length ? t("boxes.degraded", { n: left.length }) : "",
      box.waiting ? t("boxes.waiting", { n: box.waiting }) : "",
      box.removed ? t("boxes.removed") : ""].filter(Boolean);
    // a phone always shows its progress and its volume limits
    const facts = phone ? [t("boxes.positions", { n: box.positions }), phoneLimits(box.settings)].filter(Boolean) : [];
    const rename = h("button", { class: "link", onclick: async () => {
      const name = prompt(t("boxes.rename.prompt"), box.name);
      if (!name) return;
      const done = await run(() => api("PATCH", `/api/library/boxes/${box.id}`, { name }), t("common.name_changed"));
      if (done !== undefined) loadBoxes();
    } }, t("boxes.rename"));
    const takeover = h("button", { class: "link", onclick: async () => {
      const others = overview.boxes.filter((other) => other.id !== box.id).map((other) => `${other.id} (${other.name})`);
      const old = prompt(t("boxes.takeover.prompt", { boxes: others.join("\n") }));
      if (!old) return;
      const done = await run(() => api("PATCH", `/api/library/boxes/${box.id}`, { takeover: old.split(" ")[0] }),
        t("boxes.takeover.done"));
      if (done !== undefined) loadBoxes();
    } }, t("boxes.takeover"));
    const volume = h("button", { class: "link", onclick: () => openPhoneLimits(box) }, t("phones.limits"));
    const remove = h("button", { class: "link danger-text", onclick: async () => {
      if (!confirm(t("boxes.remove.confirm", { name: box.name }))) return;
      const done = await run(() => api("DELETE", `/api/library/boxes/${box.id}`), t("boxes.remove.done"));
      if (done !== undefined) loadBoxes();
    } }, t("common.remove"));
    // a phone has no slots and no update mode: it is installed by hand (docs/developer/app.md)
    return h("tr", {},
      h("td", {}, box.name, h("div", { class: "muted small" }, phone ? `${t("phones.phone")} · ${box.id}` : box.id)),
      h("td", {}, box.software || "–", !phone && ["manual", "off"].includes(status.mode)
        ? h("div", { class: "muted small" }, t(off ? "boxes.updates_off" : "boxes.manual_updates")) : ""),
      h("td", {}, when(box.contact)),
      h("td", { class: "small" }, [...notes, ...facts].join("; ") || t("boxes.positions", { n: box.positions })),
      h("td", {}, ...(isAdmin && !box.removed
        ? [rename, phone ? volume : "", overview.boxes.length > 1 ? takeover : "", remove] : [])));
  }));
  if (!overview.boxes.length) {
    $("boxes-body").append(h("tr", {}, h("td", { colSpan: 5, class: "muted" },
      overview.bootstrap ? t("boxes.none") : t("boxes.no_bootstrap"))));
  }
  // the line that sets up a new box and links it to this library (docs/developer/deployment.md, section 5)
  const offer = overview.install || {};
  const origin = window.location.origin;
  $("install-row").hidden = !offer.version;
  $("install-line").textContent = offer.version
    ? `curl -fsSLk --pinnedpubkey '${offer.pin}' ${origin}/install.sh | sudo bash -s -- --library ${origin} --pin '${offer.pin}'`
    : "";
  $("install-help").textContent = !offer.version ? t("boxes.add.none")
    : [t("boxes.add.help", { version: offer.version }),
      offer.newest !== offer.version ? t("boxes.add.older", { version: offer.version, newest: offer.newest }) : ""]
      .filter(Boolean).join(" ");
  // the app for phones: the APK of the version that "Add a box" installs, so that app and library fit
  const app = overview.app || {};
  $("phone-app-row").hidden = !app.version;
  $("phone-app-help").textContent = app.version ? t("phones.app.help", { version: app.version }) : t("phones.app.none");
  if (app.version) {
    const link = `${origin}/app.apk`;
    $("phone-app-link").textContent = link;
    $("phone-app-link").href = link;
    if ($("phone-app-qr").dataset.text !== link) {
      const qr = await run(() => api("GET", `/api/library/qr?text=${encodeURIComponent(link)}`));
      if (qr) {
        $("phone-app-qr").replaceChildren(qrImage(qr.svg, t("phones.app.qr")));
        $("phone-app-qr").dataset.text = link;
      }
    }
  }
  $("uploads-card").hidden = !overview.uploads.length;
  $("uploads-list").replaceChildren(...overview.uploads.map((upload) => h("li", {},
    t("boxes.uploads.line", { folder: upload.folder, box: upload.name, transferred: upload.transferred, n: upload.files }))));
  return active;
}

// the volume limits of a phone: the most it plays with, of what the phone is set to
function phoneLimits(settings) {
  const most = (output) => ((settings || {})[`volume.${output}`] || {})["max-volume"];
  const speaker = most("speaker");
  const headphones = most("headphones");
  if (speaker === undefined && headphones === undefined) return "";
  return t("phones.limits.line", { speaker: speaker ?? 100, headphones: headphones ?? 100 });
}

let limitsOf = null;  // the phone whose limits the dialog shows
function openPhoneLimits(box) {
  limitsOf = box;
  const form = $("phone-limits-form");
  for (const output of ["speaker", "headphones"]) {
    form[output].value = ((box.settings || {})[`volume.${output}`] || {})["max-volume"] ?? 100;
  }
  $("phone-limits-dialog").showModal();
}

$("phone-limits-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const volume = Object.fromEntries(["speaker", "headphones"].map((output) =>
    [output, { "max-volume": Number(form[output].value) }]));
  const done = await run(() => api("PATCH", `/api/library/boxes/${limitsOf.id}`, { volume }), t("limits.saved"));
  if (done === undefined) return;
  $("phone-limits-dialog").close();
  loadBoxes();
});

// pairing a phone: a QR code with the address of the library and a password for one use, valid 10 minutes
let pairingTimer = null;
$("phone-pair").addEventListener("click", async () => {
  const answer = await run(() => api("POST", "/api/library/pairings", { url: window.location.origin }));
  if (!answer) return;
  $("phone-pair-qr").replaceChildren(qrImage(answer.svg, t("phones.pair.qr")));
  $("phone-pair-qr").hidden = false;
  const until = Date.now() + answer.seconds * 1000;
  const tick = () => {
    const left = Math.round((until - Date.now()) / 1000);
    if (left <= 0) {
      clearInterval(pairingTimer);
      $("phone-pair-qr").replaceChildren();
      $("phone-pair-qr").hidden = true;
      $("phone-pair-state").textContent = t("phones.pair.expired");
      return;
    }
    $("phone-pair-state").textContent = t("phones.pair.valid", { minutes: Math.floor(left / 60), seconds: String(left % 60).padStart(2, "0") });
  };
  clearInterval(pairingTimer);
  pairingTimer = setInterval(tick, 1000);
  $("phone-pair-state").hidden = false;
  tick();
});

$("install-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("install-line").textContent);
    toast(t("boxes.add.copied"));
  } catch {
    window.getSelection().selectAllChildren($("install-line"));  // copied by hand then
  }
});

$("bootstrap-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const done = await run(() => api("PUT", "/api/library/bootstrap", { password: event.target.password.value }),
    t("access.bootstrap.saved"));
  if (done !== undefined) event.target.reset();
});

$("token-new").addEventListener("click", async () => {
  const result = await run(() => api("POST", "/api/library/tokens"));
  if (!result) return;
  $("token-value").textContent = result.token;  // shown once: the library keeps only its checksum
  $("token-value").hidden = false;
});

$("library-verify").addEventListener("click", async () => {
  if (await run(() => api("POST", "/api/library/verify"), t("access.verified"))) loadBoxes();
});

const VERSION_STATES = ["use", "manual", "dontuse"];

// the table of the versions leaves out what is of no use any more: a version that is not in the archive (the archive
// keeps the newest ones only), that no box runs or has prepared and that the library does not run itself. The choice
// is kept in this browser
const VERSIONS_KEY = "tinyjukebox.versions.all";
try {
  $("versions-hide-old").checked = localStorage.getItem(VERSIONS_KEY) !== "1";
} catch { /* a browser without storage: the old versions are hidden */ }
$("versions-hide-old").addEventListener("change", (event) => {
  try {
    localStorage.setItem(VERSIONS_KEY, event.target.checked ? "0" : "1");
  } catch { /* see above */ }
  loadVersions();
});

async function loadVersions() {
  const overview = await run(() => api("GET", "/api/library/overview"));
  if (!overview) return;
  const isAdmin = session.role === "admin";
  const names = Object.fromEntries(overview.boxes.map((box) => [box.id, box.name]));
  const releases = overview.releases;
  $("releases-state").textContent = [
    t("releases.library", { interface: overview.interface }),
    releases.checks && releases.checks.length ? t("releases.checked", {
      date: new Date(releases.checks[releases.checks.length - 1] * 1000).toLocaleString(), n: Math.round(releases.interval / 60),
    }) : t("releases.never_checked"),
  ].join(" ");
  $("releases-token-state").textContent = !releases.repository ? t("releases.no_repository")
    : [t("releases.repository", { repository: releases.repository }), tOf("releases.token_state", releases.token)].join(" ");
  $("releases-token-form").hidden = !releases.repository;
  $("releases-keep-form").versions.value = releases.keep;
  // the version the library runs itself is named in the table, also when its slot is not in the archive
  const all = overview.versions.slice().reverse();
  if (!all.some((version) => version.version === overview.library)) {
    all.push({ version: overview.library });
    all.sort((a, b) => collator.compare(b.version, a.version));
  }
  const old = (version) => Boolean(version.state) && !version.archived && version.version !== overview.library
    && !(version.boxes || []).length && !(version.prepared || []).length;
  const rows = $("versions-hide-old").checked ? all.filter((version) => !old(version)) : all;
  $("versions-hidden").textContent = rows.length < all.length ? t("releases.hidden", { n: all.length - rows.length }) : "";
  $("versions-body").replaceChildren(...rows.map((version) => {
    const runsOn = (version.boxes || []).map((id) => names[id] || id).join(", ");
    const library = version.version === overview.library ? h("b", {}, t("library.title")) : "";
    const runs = h("td", { class: "small" }, library, library && runsOn ? ", " : "", runsOn || (library ? "" : "–"));
    if (!version.state) {
      return h("tr", {}, h("td", {}, version.version), h("td", {}, "–"), h("td", {}, "–"), runs, h("td", {}, "–"), h("td"));
    }
    const select = h("select", { "aria-label": t("common.state"), disabled: !isAdmin },
      ...VERSION_STATES.map((state) => h("option", { value: state, selected: state === version.state }, state)));
    select.addEventListener("change", async () => {
      const why = prompt(t("releases.set.prompt", { version: version.version, state: select.value }), "");
      if (why === null) { select.value = version.state; return; }
      const done = await run(() => api("PUT", `/api/library/versions/${version.version}`,
        { state: select.value, why }), t("releases.set.done"));
      loadVersions();
      return done;
    });
    return h("tr", {},
      h("td", {}, version.version, version.note ? h("div", { class: "muted small" },
        version.offered === false ? t("releases.needs_newer_library") : version.note) : ""),
      h("td", { class: `state-${version.state}` }, select),
      h("td", { class: "small" }, `${version.by}, ${when(version.when)}`, h("div", { class: "muted" }, version.why)),
      runs,
      h("td", { class: "small" }, version.prepared.map((id) => names[id] || id).join(", ") || "–"),
      h("td", { class: "muted small" }, version.archived ? "" : t("releases.not_archived")));
  }));
  if (!overview.versions.length) {
    $("versions-body").append(h("tr", {}, h("td", { colSpan: 6, class: "muted" }, t("releases.none"))));
  }
}

$("releases-check").addEventListener("click", async () => {
  const result = await run(() => api("POST", "/api/library/releases/check"));
  if (!result) return;
  toast(result.found ? t("releases.found", { version: result.found }) : result.error ? errorText(result) : t("releases.nothing_new"),
    Boolean(result.error));
  loadVersions();
});

$("releases-keep-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  // what is too much leaves the archive at once
  await run(() => api("PUT", "/api/library/releases/keep", { versions: Number(event.target.versions.value) }), t("common.saved"));
  loadVersions();
});

$("releases-token-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  // the library asks the git server with the token before it keeps it, and looks for a release at once
  const result = await run(() => api("PUT", "/api/library/releases/token", { token: event.target.token.value }));
  if (!result) return;
  event.target.reset();
  toast([t("releases.token_saved"), result.found ? t("releases.found", { version: result.found }) : result.error
    ? errorText(result) : t("releases.nothing_new")].join(" "), Boolean(result.error));
  loadVersions();
});

// ---------------------------------------------------------------------------------------------------------------
// play statistics (for every user of the page): a box shows its own, the library those of all boxes or of the box
// that is chosen. What belongs to one child - a day, its hours - is never summed over the boxes

function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? t("duration.hours", { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, "0") })
    : minutes ? t("duration.minutes", { n: minutes }) : t("duration.seconds", { n: seconds });
}

function emptyRow(columns, text) {
  return h("tr", {}, h("td", { colSpan: columns, class: "muted" }, text));
}

// hours in which something was listened to, as spans: [7, 8, 15] -> [[7, 9], [15, 16]]
function spans(hours) {
  const result = [];
  for (const hour of hours.sort((a, b) => a - b)) {
    if (result.length && result[result.length - 1][1] === hour) result[result.length - 1][1] = hour + 1;
    else result.push([hour, hour + 1]);
  }
  return result;
}

// the table of the tags is sorted by the column that was clicked; a second click turns the order round. A tag
// without a value (no known time) stays at the end
const STATS_SORTS = { last: (tag) => Date.parse(tag.last) || null };
let statsTags = [];
let statsBox = "";  // the box chosen on the page of the library - none: all boxes
let statsSort = { key: "seconds", descending: true };

function showStatsTags() {
  const value = STATS_SORTS[statsSort.key] || ((tag) => tag[statsSort.key] ?? null);
  const direction = statsSort.descending ? -1 : 1;
  const sorted = statsTags.map((tag) => [value(tag), tag]).sort(([x, a], [y, b]) => {
    if (x === null || y === null) return (x === null) - (y === null) || collator.compare(a.name, b.name);
    return direction * (typeof x === "number" ? x - y : collator.compare(x, y)) || collator.compare(a.name, b.name);
  }).map(([, tag]) => tag);
  for (const head of $("stats-tags-head").querySelectorAll("th[data-sort]")) {
    if (head.dataset.sort === statsSort.key) head.setAttribute("aria-sort", statsSort.descending ? "descending" : "ascending");
    else head.removeAttribute("aria-sort");
  }
  $("stats-tags").replaceChildren(...sorted.map((tag) => h("tr", {},
    h("td", {}, tag.name),
    h("td", {}, tag.album || "–"),
    h("td", {}, `${tag.plays}×`),
    h("td", {}, duration(tag.seconds)),
    h("td", {}, tag.finished ? `${tag.finished}×` : "–"),
    h("td", {}, tag.last ? when(tag.last) : t("statistics.time_unknown")),
    h("td", { class: "stats-several small" },
      tag.boxes.map((box) => t("statistics.on_box", { box: box.name, duration: duration(box.seconds) })).join(", ")))));
  if (!sorted.length) $("stats-tags").append(emptyRow(7, t("statistics.none")));
}

function chooseStatsBox(box) {
  statsBox = box;
  loadStatistics();
}

async function loadStatistics() {
  const stats = await run(() => api("GET", !isLibrary() ? "/api/box/statistics"
    : `/api/library/statistics${statsBox ? `?box=${encodeURIComponent(statsBox)}` : ""}`));
  if (!stats) return;
  $("stats-tags-wrap").hidden = $("stats-rest").hidden = Boolean(stats.missing);
  if (stats.missing) {  // the library counts for a box that has one, and it does not answer
    $("stats-help").textContent = tOf("statistics.missing", stats.missing);
    return;
  }
  const several = isLibrary() && stats.boxes.length > 1;   // only then a box is to be chosen
  const all = several && !stats.box;
  $("stats-box").hidden = !several;
  $("stats-box").replaceChildren(h("option", { value: "" }, t("statistics.box.all")),
    ...stats.boxes.map((box) => h("option", { value: box.id, selected: box.id === stats.box }, box.name)));
  $("tab-statistics").classList.toggle("several", all);
  const shown = stats.boxes.filter((box) => !stats.box || box.id === stats.box);
  $("stats-help").textContent = [
    tOf("statistics.help", isLibrary() ? "library" : stats.origin === "box" ? "alone" : "box", { limit: stats.limit }),
    t("statistics.total", { duration: duration(shown.reduce((sum, box) => sum + box.seconds, 0)) })].join(" ");
  $("stats-boxes-card").hidden = !all;
  $("stats-boxes").replaceChildren(...stats.boxes.map((box) => h("tr", {},
    h("td", {}, h("button", { class: "link", onclick: () => chooseStatsBox(box.id) }, box.name)),
    h("td", {}, box.seconds ? duration(box.seconds) : "–"),
    h("td", {}, box.plays ? `${box.plays}×` : "–"),
    h("td", {}, box.last ? when(box.last) : box.plays ? t("statistics.time_unknown") : "–"))));
  statsTags = stats.tags.filter((tag) => tag.plays);
  showStatsTags();
  $("stats-never-card").hidden = !stats.never.length;
  $("stats-never").textContent = stats.never.join(", ");
  $("stats-days").replaceChildren(...stats.days.map((day) => h("tr", {},
    h("td", {}, new Date(`${day.day}T12:00:00`).toLocaleDateString()),
    h("td", { class: "stats-several" }, day.name),
    h("td", {}, duration(day.seconds)),
    h("td", {}, day.speaker ? duration(day.speaker) : "–"),
    h("td", {}, day.headphones ? duration(day.headphones) : "–"),
    h("td", { class: "small" }, spans(Object.keys(day.hours).map(Number))
      .map(([from, to]) => t("statistics.hours", { from, to })).join(", ")))));
  if (!stats.days.length) $("stats-days").append(emptyRow(6, t("statistics.days.none")));
  $("stats-unknown").textContent = stats.unknown_time
    ? t("statistics.unknown_time", { duration: duration(stats.unknown_time) }) : "";
  $("stats-stations").replaceChildren(...stats.stations.map((station) => h("tr", {},
    h("td", {}, station.name), h("td", {}, duration(station.seconds)))));
  if (!stats.stations.length) $("stats-stations").append(emptyRow(2, t("statistics.stations.none")));
  $("stats-skipped").replaceChildren(...stats.skipped.map((track) => h("tr", {},
    h("td", {}, track.title), h("td", {}, track.track), h("td", {}, `${track.count}×`))));
  if (!stats.skipped.length) $("stats-skipped").append(emptyRow(3, t("statistics.skipped.none")));
  const decimal = (value) => value ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "–";
  $("stats-versions").replaceChildren(...stats.versions.map((version) => h("tr", {},
    h("td", {}, version.version), h("td", {}, version.starts), h("td", {}, version.errors),
    h("td", {}, version.fallbacks), h("td", {}, decimal(version.installed / 86400)),
    h("td", {}, decimal(version.running / 3600)), h("td", {}, decimal(version.active / 3600)))));
}

$("stats-box").addEventListener("change", (event) => chooseStatsBox(event.target.value));

for (const head of $("stats-tags-head").querySelectorAll("th[data-sort]")) {
  const sort = () => {
    const key = head.dataset.sort;
    statsSort = { key, descending: statsSort.key === key ? !statsSort.descending : !["name", "album"].includes(key) };
    showStatsTags();
  };
  head.tabIndex = 0;
  head.addEventListener("click", sort);
  head.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    sort();
  });
}

// ---------------------------------------------------------------------------------------------------------------
// hardware: what the drivers say about the buttons, the led and the rfid reader (tab "System", admins) - asked twice
// a second for as long as the tab is open

const LIT_COLORS = {
  "000": "BLACK", "111": "WHITE", "100": "RED", "010": "GREEN", "001": "BLUE", "110": "YELLOW", "011": "AQUA",
  "101": "MAGENTA",
};
const HARDWARE_INTERVAL = 500;  // ms
let hardwareTimer = null;
let hardwareTurn = 0;  // a tab opened again starts a new turn - an answer of the one before plans nothing

function agoText(seconds) {
  const n = Math.max(0, Math.round(seconds));
  if (n < 120) return t("hardware.ago.seconds", { n });
  if (n < 7200) return t("hardware.ago.minutes", { n: Math.round(n / 60) });
  return t("hardware.ago.hours", { n: Math.round(n / 3600) });
}

// a time of the box (seconds since 1970) and how long ago that was - by the clock of the box, not of the browser
const hardwareClock = (at) => new Date(at * 1000).toLocaleTimeString();

const fact = (label, ...value) => [h("dt", {}, label), h("dd", {}, ...value)];

function renderHardware(info) {
  $("hw-none").hidden = info.available;
  $("hw-view").hidden = !info.available;
  if (!info.available) return;
  const driver = (d) => `${d.name} · ${t(d.running ? "hardware.driver.running" : "hardware.driver.stopped")}`;
  const level = (pressed) => pressed === null
    ? h("span", { class: "badge badge-warn" }, tOf("hardware.level", "unknown"))
    : h("span", { class: `badge badge-${pressed ? "busy" : "idle"}` }, tOf("hardware.level", pressed ? "pressed" : "open"));
  $("hw-buttons").replaceChildren(...info.buttons.map((b) => h("tr", {},
    h("td", {}, tOf("hardware.button", b.button)),
    h("td", {}, h("code", {}, b.pin)),
    h("td", {}, level(b.pressed)),
    h("td", {}, b.last_event ? t("hardware.event_at", {
      event: tOf("hardware.event", b.last_event), time: hardwareClock(b.last_event_at),
      ago: agoText(info.time - b.last_event_at),
    }) : t("hardware.event_none")),
    h("td", {}, b.presses),
    h("td", {}, driver(b)))));
  $("hw-leds").replaceChildren(...info.leds.map((led) => {
    const steps = led.colors.map((color, i) => (led.delays.length
      ? t("hardware.led.step", { color, seconds: led.delays[i] }) : color));
    const lit = led.lit;
    const lights = ["red", "green", "blue"];
    const on = lit ? lights.filter((name) => lit[name]) : [];
    const color = lit ? LIT_COLORS[lights.map((name) => (lit[name] ? "1" : "0")).join("")] : null;
    return h("dl", { class: "facts" },
      fact(t("hardware.column.driver"), driver(led)),
      fact(t("hardware.led.pattern"), steps.join(" → ")),
      fact(t("hardware.led.lit"), ...(lit
        ? [h("span", { class: "led hw-dot", style: `--led-a: ${LED_COLORS[color]}` }),
          on.length ? on.map((name) => tOf("hardware.light", name)).join(" + ") : t("hardware.led.off"),
          " ", h("code", {}, color)]
        : [tOf("hardware.level", "unknown")])),
      fact(t("hardware.pins"), lights.map((name) => t("hardware.pin", {
        name: tOf("hardware.light", name), pin: led.pins[name],
      })).join(", ")));
  }));
  $("hw-readers").replaceChildren(...info.readers.map((reader) => h("dl", { class: "facts" },
    fact(t("hardware.column.driver"), driver(reader)),
    fact(t("hardware.reader.program"), t(reader.reader ? "hardware.driver.running" : "hardware.reader.not_running")),
    fact(t("hardware.reader.tag"), reader.uid ? h("code", {}, reader.uid) : t("hardware.reader.no_tag")),
    reader.error ? fact(t("hardware.reader.error"), reader.error) : null,
    fact(t("hardware.reader.changed"), reader.changed_at ? t("hardware.time", {
      time: hardwareClock(reader.changed_at), ago: agoText(info.time - reader.changed_at),
    }) : "–"),
    fact(t("hardware.pins"), Object.entries(reader.pins).map(([name, pin]) => t("hardware.pin", {
      name: name.replace("pin_", "").toUpperCase(), pin,
    })).join(", ")))));
}

async function loadHardware() {
  const turn = ++hardwareTurn;
  clearTimeout(hardwareTimer);
  const wanted = () => turn === hardwareTurn && session && session.role === "admin" && !isLibrary()
    && currentTab === "system";
  if (!wanted()) return;
  if (!document.hidden) {
    try {
      const info = await api("GET", "/api/box/hardware");
      if (wanted()) renderHardware(info);
    } catch {
      // the box restarts, or the session has ended: the next turn tries again, the login ends the asking
    }
  }
  if (wanted()) hardwareTimer = setTimeout(loadHardware, HARDWARE_INTERVAL);
}

// ---------------------------------------------------------------------------------------------------------------
// system

async function loadSystem() {
  refreshStatus();
  if (session.role === "admin") loadUsers();
  if (session.role === "admin") loadBackups();
  if (session.role === "admin") loadRestore();
  if (isLibrary()) return;
  loadLibrary();
  if (session.role === "admin") loadVersion();
  if (session.role === "admin") loadHardware();
  const [activities, albums, tags] = await Promise.all([
    run(() => api("GET", "/api/activities")), run(() => api("GET", "/api/albums")), run(() => api("GET", "/api/tags")),
  ]);
  if (!activities) return;
  const albumTitle = Object.fromEntries((albums || []).map((a) => [a.id, a.title]));
  const tagName = Object.fromEntries((tags || []).map((tag) => [tag.uid, tag.name]));
  $("activities-body").replaceChildren(...activities.map((a) => h("tr", {},
    h("td", {}, tagName[a.tag_uid] || h("code", {}, a.tag_uid)),
    h("td", {}, albumTitle[a.album_id] || a.album_id),
    h("td", {}, a.track_id + 1),
    h("td", {}, formatTime(a.track_time)),
    h("td", {}, a.finished ? t("common.yes") : t("common.no")))));
}

// the backups that the library, or a box without a library, makes by itself (docs/developer/operations.md,
// section 10.2)
function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return t("common.size_unknown");  // e.g. a release without the size of its archive
  const units = ["Bytes", "kB", "MB", "GB"];
  let value = bytes, unit = 0;
  while (value >= 1000 && unit < units.length - 1) { value /= 1000; unit += 1; }
  const digits = unit && value < 10 ? 1 : 0;
  return `${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${units[unit]}`;
}

// "2026-10-10 03:00", the local time of the library
const toTheMinute = (text) => new Date(text.replace(" ", "T")).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
let backupTimer = null;   // looks again while a backup that was asked for on this page is being made
const BACKUP_KINDS = ["plain", "albums"];  // the two schedules: without the albums, with them
const backupsPath = () => (isLibrary() ? "/api/library/backups" : "/api/backups");

async function loadBackups(fill = true) {
  const status = await run(() => api("GET", backupsPath()));
  if (status) showBackups(status, fill);
}

function showBackups(status, fill = true) {
  // a box that works with a library leaves the backups to it
  $("backup-kept").hidden = !status.active;
  const form = $("backup-form");
  for (const kind of BACKUP_KINDS) {
    const schedule = status[kind];
    const field = (name) => form.elements[`${kind}-${name}`];
    if (fill) {
      field("enabled").checked = schedule.enabled;
      for (const name of ["every", "unit", "time", "keep", "folder"]) field(name).value = schedule[name];
      field("folder").placeholder = t("backup.folder.placeholder", { folder: schedule.default_folder });
    }
    const last = schedule.last;
    $(`backup-state-${kind}`).textContent = [
      t("backup.state.folder", { folder: schedule.folder }),
      last ? (last.ok ? t("backup.state.last", { when: when(last.when) })
        : t("backup.state.failed", { when: when(last.when), message: last.message })) : "",
      schedule.next ? t("backup.state.next", { when: toTheMinute(schedule.next) }) : "",
    ].filter(Boolean).join(" ");
  }
  $("backup-state").textContent = status.running ? t("backup.state.running") : "";
  $("backup-now").disabled = $("backup-now-albums").disabled = status.running;
  $("backups-body").replaceChildren(...status.backups.map((backup) => {
    const path = `${backupsPath()}/${encodeURIComponent(backup.name)}`;
    return h("tr", {},
      h("td", {}, toTheMinute(backup.when)),
      h("td", {}, backup.albums ? t("backup.with_albums") : t("backup.without_albums")),
      h("td", {}, formatSize(backup.size)),
      h("td", {},
        h("a", { href: path, download: backup.name, title: backup.name }, t("common.download")), " ",
        h("button", {
          class: "link", onclick: async () => {
            const status = await run(() => api("POST", "/api/restore", { name: backup.name }));
            if (!status) return;
            restoreAsk = true;  // the page asks as soon as the backup is unpacked
            showRestore(status);
          },
        }, t("restore.put_back")), " ",
        h("button", {
          class: "link danger-text", onclick: async () => {
            if (!confirm(t("backup.delete.confirm", { when: toTheMinute(backup.when) }))) return;
            await run(() => api("DELETE", path), t("backup.deleted"));
            loadBackups(false);
          },
        }, t("common.delete"))));
  }));
  if (!status.backups.length) {
    $("backups-body").append(h("tr", {}, h("td", { colSpan: 4, class: "muted" }, t("backup.none"))));
  }
  clearTimeout(backupTimer);
  if (status.running && backupTimer !== null && currentTab === "system") {
    backupTimer = setTimeout(() => loadBackups(false), 2000);
  } else {
    backupTimer = null;
  }
}

$("backup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const schedule = (kind) => {
    const field = (name) => form.elements[`${kind}-${name}`];
    return {
      enabled: field("enabled").checked, every: Number(field("every").value), unit: field("unit").value,
      time: field("time").value, keep: Number(field("keep").value), folder: field("folder").value.trim(),
    };
  };
  const status = await run(() => api("PUT", backupsPath(), {
    plain: schedule("plain"), albums: schedule("albums"),
  }), t("common.saved"));
  if (status) showBackups(status);
});

for (const [id, albums] of [["backup-now", false], ["backup-now-albums", true]]) {
  $(id).addEventListener("click", async () => {
    const status = await run(() => api("POST", backupsPath(), { albums }), t("backup.started"));
    if (!status) return;
    backupTimer = 0;  // from now on the page looks until this backup is made
    showBackups(status, false);
  });
}

// putting a backup back (docs/developer/operations.md, section 10.3): the backup is unpacked beside the data, the page
// asks once, and the box or the library starts again with it
let restoreTimer = null;
let restoreAsk = false;  // this page unpacked the backup: it asks at once whether to put it back

async function loadRestore() {
  const status = await run(() => api("GET", "/api/restore"));
  if (status) showRestore(status);
}

function showRestore(status) {
  const busy = status.state === "staging" || status.state === "confirmed";
  const lines = [];
  if (status.state === "staging") lines.push(t("restore.state.staging"));
  if (status.state === "staged") lines.push(t("restore.state.staged", { when: when(status.about.created) }));
  if (status.state === "idle" && status.error) lines.push(t("restore.state.failed", { message: status.error }));
  const last = status.last;
  if (last) {
    lines.push(t("restore.state.last", { when: when(last.done), created: when(last.backup.created) }));
    if (last.missing && last.missing.length) {
      lines.push(t("restore.state.missing", {
        n: last.missing.length, albums: last.missing.map((album) => `${album.title} (${album.folder})`).join(", "),
      }));
    }
  }
  $("restore-state").textContent = lines.join(" ");
  $("restore-file").disabled = busy;
  $("restore-confirm").hidden = $("restore-discard").hidden = status.state !== "staged";
  clearTimeout(restoreTimer);
  if (status.state === "staging" && currentTab === "system") restoreTimer = setTimeout(loadRestore, 2000);
  if (status.state === "staged" && restoreAsk) {
    restoreAsk = false;
    askRestore(status);
  }
}

// asks once; a backup that is not put back is discarded
async function askRestore(status) {
  const about = status.about;
  const text = [
    t("restore.confirm", { when: when(about.created) }),
    about.albums ? t("restore.confirm.albums") : "",
    status.library ? t("restore.confirm.library") : "",
    t("restore.confirm.backup"),
  ].filter(Boolean).join("\n\n");
  if (!confirm(text)) {
    const discarded = await run(() => api("DELETE", "/api/restore"), t("restore.discarded"));
    if (discarded) showRestore(discarded);
    return;
  }
  const confirmed = await run(() => api("POST", "/api/restore/confirm"));
  if (!confirmed) return;
  $("restore-state").textContent = t("restore.state.restarting");
  $("restore-confirm").hidden = $("restore-discard").hidden = true;
  waitForRestart();
}

// the program stops and starts again: the page waits until it is gone and back, then loads anew
function waitForRestart() {
  let gone = false;
  const started = Date.now();
  const look = async () => {
    try {
      const response = await fetch("/api/session", { credentials: "same-origin", cache: "no-store" });
      if ((gone || Date.now() - started > 60000) && response.status < 500) {
        location.reload();
        return;
      }
      gone = gone || response.status >= 500;
    } catch {
      gone = true;
    }
    setTimeout(look, 1500);
  };
  setTimeout(look, 1500);
}

$("restore-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  $("restore-state").textContent = t("restore.state.uploading", { size: formatSize(file.size) });
  $("restore-file").disabled = true;
  let response;
  uploadsRunning += 1;  // a backup with its albums may take longer than the idle timeout: no absence
  try {
    response = await fetch("/api/restore", {
      method: "PUT", credentials: "same-origin", body: file,
      headers: { ...requestHeaders(), "Content-Type": "application/octet-stream" },
    });
  } catch {
    toast(t("restore.send_failed"), true);
    loadRestore();
    return;
  } finally {
    uploadsRunning -= 1;
    noteInput();
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(errorText(data, response.status), true);
    loadRestore();
    return;
  }
  restoreAsk = true;
  showRestore(data);
});

$("restore-confirm").addEventListener("click", async () => {
  const status = await run(() => api("GET", "/api/restore"));
  if (status && status.state === "staged") askRestore(status);
});

$("restore-discard").addEventListener("click", async () => {
  const status = await run(() => api("DELETE", "/api/restore"), t("restore.discarded"));
  if (status) showRestore(status);
});

// the own password (every user)
$("own-password").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.password.value !== form.repeat.value) {
    return toast(t("password.mismatch"), true);
  }
  try {
    await api("PUT", "/api/session/password", { current_password: form.current.value, password: form.password.value });
  } catch (e) {
    if (e.status === 401) return;  // the login is shown
    return toast(e.status === 403 ? t("password.wrong") : e.status === 429 ? t("login.error.too_many") : e.message, true);
  }
  form.reset();
  toast(t("password.changed"));
});

// users of the web page (admins)
async function loadUsers() {
  const users = await run(() => api("GET", "/api/users"));
  if (!users) return;
  $("users-body").replaceChildren(...users.map((user) => {
    const path = `/api/users/${encodeURIComponent(user.username)}`;
    const isSelf = user.username === session.username;
    const password = h("input", {
      type: "password", class: "inline-input", placeholder: t("users.password.placeholder"), minLength: 8, required: true,
      autocomplete: "new-password", "aria-label": t("users.password.label", { name: user.username }),
    });
    const form = h("form", { class: "user-password", onsubmit: async (event) => {
      event.preventDefault();
      const result = await run(() => api("PUT", `${path}/password`, { password: password.value }),
        t("users.password.done", { name: user.username }));
      if (result !== undefined) form.reset();
    } }, password, h("button", { type: "submit" }, t("users.password.submit")));
    const remove = h("button", {
      class: "link danger-text", disabled: isSelf, title: isSelf ? t("users.remove.self") : "",
      onclick: async () => {
        if (!confirm(t("users.remove.confirm", { name: user.username }))) return;
        const result = await run(() => api("DELETE", path), t("users.removed"));
        if (result !== undefined) loadUsers();
      },
    }, t("common.delete"));
    return h("tr", {},
      h("td", {}, user.username, isSelf ? h("span", { class: "muted small" }, ` ${t("users.self")}`) : ""),
      h("td", {}, user.role),
      h("td", {}, form),
      h("td", {}, remove));
  }));
}

$("user-new").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const user = await run(() => api("POST", "/api/users", {
    username: form.username.value.trim(), password: form.password.value, role: form.role.value,
  }));
  if (user) {
    toast(t("users.created", { name: user.username }));
    form.reset();
    loadUsers();
  }
});

$("sys-sanity").addEventListener("click", async () => {
  const result = await run(() => api("POST", "/api/sanity-check"));
  if (!result) return;
  const output = $("sys-output");
  output.hidden = false;
  output.textContent = (result.passed ? t("system.sanity.ok") : t("system.sanity.problems")) +
    (result.messages.length ? `\n\n${result.messages.join("\n")}` : "");
});

$("sys-rebuild").addEventListener("click", async () => {
  if (!confirm(t("system.rebuild.confirm"))) return;
  await run(() => api("POST", "/api/activities/rebuild"), t("system.rebuild.done"));
  loadSystem();
});

// ---------------------------------------------------------------------------------------------------------------
// start

(async () => {
  await loadTexts();
  await loadIdentity();
  try {
    showApp(await api("GET", "/api/session"));
  } catch {
    showLogin();
  }
})();
