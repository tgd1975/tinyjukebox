# Todos

Nächste Kennung: TD-12

Was nur du selbst tun kannst: Konto, Zahlung, Freigabe, Hardware in der Hand. Was ein Chat selbst kann, gehört nicht
hierher. Eine Zeile je Todo: `- [ ] TD-<n> - **Was:** Befehl oder Link – warum. *(Chat)*`; wer eine Zeile anlegt,
nimmt die nächste Kennung und zählt sie oben hoch, keine wird wiederverwendet. Vor jeder Änderung die Datei frisch
lesen und nur eigene Zeilen ändern. Erledigt sofort abhaken, mit Datum am Ende
(`- [x] TD-<n> - **Was:** was daraus wurde (Commit). [JJJJ-MM-TT]`, ein fallengelassenes sagt es), und nach
„Erledigt“ am Ende verschieben, das neueste oben – egal welcher Chat es erfährt. Keine Zeile löschen.

## Für dich

- [ ] TD-1 - **Radio auf der Box prüfen (0.44.0):** Box einschalten, damit sie 0.44.0 holt, dann ein paar verschiedene
  Sender auflegen (direkter Stream, M3U/PLS, HLS; Lautsprecher und Kopfhörer). Die LED soll grün → aqua → blau wechseln,
  bis der Ton kommt, dann blau. Danach in der Bibliothek, Tab „Log“, die Box wählen, Code `radio.startup`, „Als Datei
  speichern“. Warum: nur damit lässt sich entscheiden, ob die Startverzögerung (2 s Puffer) so bleibt
  (`docs/developer/roadmap.md`, Abschnitt 2). *(tinyjukebox-fa)*
- [ ] TD-3 - **Paket 0.46.0 auf dem NAS installieren:** `cd ~/Dokumente/tinyjukebox && git pull && ./update_library.sh
  <nas>` (fragt Benutzer und Passwort von DSM; beim ersten Mal den Fingerprint mit dem im Browser vergleichen). Warum:
  die Boxen holen 0.46.0 von selbst – die Korrekturen von 0.45.0 an der Bibliothek selbst (u. a. der Ordner eines
  Albums, der aus dem Album-Ordner herausführen konnte), ihre neuen Farben und ihre Hilfe wirken erst mit dem neuen
  Paket. *(tinyjukebox-2a)*
- [ ] TD-4 - **GitHub-Umgebung `release` anlegen:** <https://github.com/tgd1975/tinyjukebox/settings/environments> → New
  environment `release`, Deployment branches nur `master`, keine Reviewer; `SIGNING_KEY`, `APP_SIGNING_KEY` und
  `APP_SIGNING_PASSWORD` dorthin, die Repository-Secrets dann löschen; optional beim Trusted Publisher von PyPI die
  Umgebung `release` eintragen. Warum: ab 0.45.0 veröffentlicht ein eigener Job in dieser Umgebung, und nur `master`
  soll die echten Schlüssel bekommen. *(tinyjukebox-2a)*
- [ ] TD-6 - **Review-Korrekturen auf der Box prüfen:** `docs/developer/roadmap.md`, Abschnitt 1, die Zeilen mit „0.45“:
  Headset weg und wieder da, die Tabelle zu `rfid-reader` (Tags tauschen und stapeln, Reset des RC522, `sudo pkill -STOP
  rfid-reader`), die Zeilen zu Spotify. Warum: Chip, Funk und Soloist gibt es in der Cloud nicht – getestet ist nur mit
  Stand-ins. *(tinyjukebox-2a)*
- [ ] TD-7 - **App 0.45.0 auf einem Telefon prüfen:** die APK aus „Telefon hinzufügen“ der Bibliothek installieren, dann
  die Zeilen mit „(0.45)“ der App-Tabelle in `docs/developer/roadmap.md`, Abschnitt 1 (wegwischen, Tag im Hintergrund
  tauschen, andere Controller, Download im Flugmodus). Warum: der Android-Teil ließ sich ohne SDK weder bauen noch
  ausprobieren. *(tinyjukebox-2a)*
- [ ] TD-8 - **Schlüssel von DSM und Bibliothek einmal bestätigen:** beim nächsten `./update_library.sh <nas>` bzw.
  `./release.sh <nas>` den gezeigten Fingerprint mit dem im Browser vergleichen, beim ersten `scripts/check_release.sh`
  den Schlüssel mit dem Pin im Tab „Boxen“. Warum: die Skripte senden Passwort und Token nur noch an einen bekannten
  Schlüssel (`~/.config/tinyjukebox/pins`). *(tinyjukebox-2a)*
- [ ] TD-9 - **Nur wenn Docker Compose genutzt wird:** neben `deploy/docker/compose.yaml` die Dateien `admin-password`
  und `bootstrap-password` anlegen, lesbar für uid 1000 (`openssl rand -hex 12 > admin-password`, `openssl rand -hex 24
  > bootstrap-password`). Warum: compose übergibt die Passwörter als Secrets, nicht mehr über die Umgebung.
  *(tinyjukebox-2a)*
- [ ] TD-10 - **Entscheiden, ob das Testbett der Slot-Skripte neu gebaut wird:** `docs/developer/library.md`, Abschnitt
  9.8. Warum: 0.45.0 ändert die Notizen, den Link ins Leere (R7) und die Ablehnung von Releases ohne dieses Testbett;
  die Fälle mit Stromausfall sind nur zum Teil automatisch getestet. *(tinyjukebox-2a)*

## Erledigt

- [x] TD-5 - **Installer nach 0.45.0 auf der Box laufen lassen:** `./update_box.sh` ist gelaufen; `rfid-reader`
  startet als root über den geprüften Starter. [2026-11-07]
- [x] TD-2 - **Installer auf der Box laufen lassen:** mit dem Lauf von TD-5 erledigt – Notfall-Modus und die Meldung
  „Box startet nicht“ sind mit den Start-Skripten auf der Box. [2026-11-07]
- [x] TD-11 - **DSM in den Vault:** `vault_dsm_address`, `vault_dsm_user` und `vault_dsm_password` stehen im
  Vault; `./update_library.sh` und `./release.sh` brauchen kein NAS mehr und fragen nur nach dem Vault-Passwort
  (09d5212). [2026-11-07]
