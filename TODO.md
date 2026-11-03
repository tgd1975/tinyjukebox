# Todos

Was nur du selbst tun kannst: Konto, Zahlung, Freigabe, Hardware in der Hand. Was ein Chat selbst kann, gehört nicht
hierher. Eine Zeile je Todo: `- [ ] **Was:** Befehl oder Link – warum. *(Chat)*`. Vor jeder Änderung die Datei frisch
lesen und nur eigene Zeilen ändern; erledigt sofort abhaken (`- [x]`), der Chat, der die Zeile eingetragen hat, löscht
abgehakte Zeilen beim nächsten Lesen.

## Für dich

- [ ] **Radio auf der Box prüfen (0.44.0):** Box einschalten, damit sie 0.44.0 holt, dann ein paar verschiedene
  Sender auflegen (direkter Stream, M3U/PLS, HLS; Lautsprecher und Kopfhörer). Die LED soll grün → aqua → blau
  wechseln, bis der Ton kommt, dann blau. Danach in der Bibliothek, Tab „Log“, die Box wählen, Code `radio.startup`,
  „Als Datei speichern“. Warum: nur damit lässt sich entscheiden, ob die Startverzögerung (2 s Puffer) so bleibt
  (`docs/developer/roadmap.md`, Abschnitt 2). *(tinyjukebox-fa)*
- [ ] **Installer auf der Box laufen lassen:** `cd ~/Dokumente/tinyjukebox && ./update_box.sh` (fragt das
  become-Passwort). Warum: Notfall-Modus und die Meldung „Box startet nicht“ an die Bibliothek (0.40.0/0.41.0)
  stecken in den Start-Skripten, und die kommen nur mit dem Installer auf die Box. *(tinyjukebox-35)*
- [ ] **Version festlegen, dann den Branch nach master bringen:** Branch `ccr-d72b3d79-92m3sc` prüfen, danach
  `./release.sh <nas>`. Eingetragen ist 0.45.0; nach `docs/developer/development.md`, Abschnitt 8, kann die Änderung
  von `deploy/docker/compose.yaml` (zwei Passwort-Dateien nötig) als „major“ zählen – dann 1.0.0 in `pyproject.toml`
  und `CHANGELOG.md`, `uv lock`. Warum: erst der Push nach master macht das Release. *(tinyjukebox-2a)*
- [ ] **GitHub-Umgebung `release` anlegen:** <https://github.com/tgd1975/tinyjukebox/settings/environments> → New
  environment `release`, Deployment branches nur `master`, keine Reviewer; `SIGNING_KEY`, `APP_SIGNING_KEY` und
  `APP_SIGNING_PASSWORD` dorthin, die Repository-Secrets dann löschen; optional beim Trusted Publisher von PyPI die
  Umgebung `release` eintragen. Warum: ab 0.45.0 veröffentlicht ein eigener Job in dieser Umgebung, und nur `master` soll die
  echten Schlüssel bekommen. *(tinyjukebox-2a)*
- [ ] **Installer nach 0.45.0 auf der Box laufen lassen** (ein Lauf erledigt auch den Eintrag „Installer auf der Box
  laufen lassen“ oben): `cd ~/Dokumente/tinyjukebox && ./update_box.sh`, dann
  `readlink /usr/local/bin/rfid-reader` (→ `/usr/local/lib/tinyjukebox/rfid-reader`),
  `cat /usr/local/lib/tinyjukebox/rfid-reader.trusted` (nennt die Versionen der Slots) und im Log
  `RFID - rfid-reader is ready`. Warum: rfid-reader läuft als root nur noch über den geprüften Starter, und der kommt
  nur mit dem Installer auf die Box. *(tinyjukebox-2a)*
- [ ] **Review-Korrekturen auf der Box prüfen:** `docs/developer/roadmap.md`, Abschnitt 1, die Zeilen mit „0.45“:
  Headset weg und wieder da, die Tabelle zu `rfid-reader` (Tags tauschen und stapeln, Reset des RC522,
  `sudo pkill -STOP rfid-reader`), die Zeilen zu Spotify. Warum: Chip, Funk und Soloist gibt es in der Cloud nicht –
  getestet ist nur mit Stand-ins. *(tinyjukebox-2a)*
- [ ] **App 0.45.0 auf einem Telefon prüfen:** die APK aus „Telefon hinzufügen“ der Bibliothek installieren, dann die
  Zeilen mit „(0.45)“ der App-Tabelle in `docs/developer/roadmap.md`, Abschnitt 1 (wegwischen, Tag im Hintergrund
  tauschen, andere Controller, Download im Flugmodus). Warum: der Android-Teil ließ sich ohne SDK weder bauen noch
  ausprobieren. *(tinyjukebox-2a)*
- [ ] **Schlüssel von DSM und Bibliothek einmal bestätigen:** beim nächsten `./update_library.sh <nas>` bzw.
  `./release.sh <nas>` den gezeigten Fingerprint mit dem im Browser vergleichen, beim ersten
  `scripts/check_release.sh` den Schlüssel mit dem Pin im Tab „Boxen“. Warum: die Skripte senden Passwort und Token
  nur noch an einen bekannten Schlüssel (`~/.config/tinyjukebox/pins`). *(tinyjukebox-2a)*
- [ ] **Nur wenn Docker Compose genutzt wird:** neben `deploy/docker/compose.yaml` die Dateien `admin-password` und
  `bootstrap-password` anlegen, lesbar für uid 1000 (`openssl rand -hex 12 > admin-password`,
  `openssl rand -hex 24 > bootstrap-password`). Warum: compose übergibt die Passwörter als Secrets, nicht mehr über
  die Umgebung. *(tinyjukebox-2a)*
- [ ] **Entscheiden, ob das Testbett der Slot-Skripte neu gebaut wird:** `docs/developer/library.md`, Abschnitt 9.8.
  Warum: 0.45.0 ändert die Notizen, den Link ins Leere (R7) und die Ablehnung von Releases ohne dieses Testbett; die
  Fälle mit Stromausfall sind nur zum Teil automatisch getestet. *(tinyjukebox-2a)*
