# Todos

Nächste Kennung: TD-14

Was nur du selbst tun kannst: Konto, Zahlung, Freigabe, Hardware in der Hand. Was ein Chat selbst kann, gehört nicht
hierher. Eine Zeile je Todo: `- [ ] TD-<n> - **Was:** Befehl oder Link – warum. *(Chat)*`; wer eine Zeile anlegt,
nimmt die nächste Kennung und zählt sie oben hoch, keine wird wiederverwendet. Vor jeder Änderung die Datei frisch
lesen und nur eigene Zeilen ändern. Erledigt sofort abhaken, mit Datum am Ende
(`- [x] TD-<n> - **Was:** was daraus wurde (Commit). [JJJJ-MM-TT]`, ein fallengelassenes sagt es), und nach
„Erledigt“ am Ende verschieben, das neueste oben – egal welcher Chat es erfährt. Keine Zeile löschen.

## Für dich

- [ ] TD-7 - **App 0.45.0 auf einem Telefon prüfen:** die APK aus „Telefon hinzufügen“ der Bibliothek installieren, dann
  die Zeilen mit „(0.45)“ der App-Tabelle in `docs/developer/roadmap.md`, Abschnitt 1 (wegwischen, Tag im Hintergrund
  tauschen, andere Controller, Download im Flugmodus). Warum: der Android-Teil ließ sich ohne SDK weder bauen noch
  ausprobieren. *(tinyjukebox-2a)*

## Erledigt

- [x] TD-9 - **Passwort-Dateien für Docker Compose:** fallengelassen – Docker Compose wird nicht genutzt; wer es
  nutzt, legt `admin-password` und `bootstrap-password` neben `deploy/docker/compose.yaml` an. [2026-11-08]
- [x] TD-6 - **Review-Korrekturen von 0.45 auf der Box:** Headset weg und wieder da und die Tabelle zu `rfid-reader`
  auf der Box geprüft, alles wie erwartet (`docs/developer/roadmap.md`, Abschnitt 1). Die Zeilen zu Spotify von
  0.45 bleiben dort offen, bis Spotify einmal auf der Box spielt. [2026-11-08]
- [x] TD-8 - **Schlüssel von DSM und Bibliothek bestätigt:** beide Fingerprints mit dem Browser verglichen, der
  Pin der Bibliothek mit dem im Tab „Boxen“; beide stehen in `~/.config/tinyjukebox/pins` (DSM `:5001`,
  Bibliothek `:8866`). [2026-11-08]
- [x] TD-1 - **Radio auf der Box prüfen (0.44.0):** acht Starts eines direkten Streams (ORF): Ton nach 1,4–9,1 s,
  meist etwa 4 s, keine Aussetzer; der 2-s-Puffer bleibt, er kostet bei diesem Sender keine Zeit – die Zeit geht vor
  der ersten Ausgabe von ffmpeg auf der Box verloren (`docs/developer/roadmap.md`, Abschnitt 2). [2026-11-07]
- [x] TD-13 - **Schlüssel der App im Vault:** `vault_app_signing_key` und `vault_app_signing_password` stehen im
  Vault – die zweite Kopie neben `~/.config/tinyjukebox/`. [2026-11-07]
- [x] TD-10 - **Testbett der Slot-Skripte:** entschieden – passen die Slot-Skripte nicht mehr dazu, wird es neu
  gebaut, vor jeder Änderung über eine Korrektur hinaus (`docs/developer/roadmap.md`, Abschnitt 3;
  `docs/developer/library.md`, Abschnitt 9.8). [2026-11-07]
- [x] TD-12 - **PyPI: Trusted Publisher für `tinyjukebox`:** angelegt; der wiederholte Job „publish“ von 0.47.0 lief
  durch, PyPI hat `tinyjukebox` 0.47.0. [2026-11-07]
- [x] TD-4 - **GitHub-Umgebung `release`:** nur `master` darf sie nutzen, keine Reviewer; sie hat `SIGNING_KEY`,
  `APP_SIGNING_KEY` und `APP_SIGNING_PASSWORD` (der Schlüssel der App neu angelegt), das Repository-Secret ist
  gelöscht (4f4890b, 99da1f4). Trusted Publisher bei PyPI: siehe TD-12. [2026-11-07]
- [x] TD-3 - **Paket 0.46.0 auf dem NAS installieren:** das Paket von 0.46.0 läuft auf dem NAS. [2026-11-07]
- [x] TD-5 - **Installer nach 0.45.0 auf der Box laufen lassen:** `./update_box.sh` ist gelaufen; `rfid-reader`
  startet als root über den geprüften Starter. [2026-11-07]
- [x] TD-2 - **Installer auf der Box laufen lassen:** mit dem Lauf von TD-5 erledigt – Notfall-Modus und die Meldung
  „Box startet nicht“ sind mit den Start-Skripten auf der Box. [2026-11-07]
- [x] TD-11 - **DSM in den Vault:** `vault_dsm_address`, `vault_dsm_user` und `vault_dsm_password` stehen im
  Vault; `./update_library.sh` und `./release.sh` brauchen kein NAS mehr und fragen nur nach dem Vault-Passwort
  (09d5212). [2026-11-07]
