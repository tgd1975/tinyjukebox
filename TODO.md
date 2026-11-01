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
