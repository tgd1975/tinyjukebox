# Tiny Jukebox – Verwaltung

Für die Person, die zu Hause die Boxen und die Bibliothek einrichtet und betreibt: mit einem Terminal, einem NAS oder
einem PC und einem Browser. Was zu tun ist, Schritt für Schritt, und was dann passiert. Wie man die Box bedient und
was die Webseite bietet, steht im [Benutzerhandbuch](manual.md); hier wird darauf verwiesen statt es zu wiederholen.
Jedes Detail – jeder Befehl, jede Option, jede Datei – steht in den Entwickler-Dokumenten
([`docs/developer/`](../../developer/operations.md), auf Englisch).

Inhalt:

1. [Überblick](#1-überblick)
2. [Eine Box einrichten](#2-eine-box-einrichten)
3. [Die Bibliothek](#3-die-bibliothek)
4. [Updates](#4-updates)
5. [Benutzer und Zugang](#5-benutzer-und-zugang)
6. [Daten und Sicherungen](#6-daten-und-sicherungen)
7. [Logs und Diagnose](#7-logs-und-diagnose)
8. [Konfiguration](#8-konfiguration)
9. [Wenn etwas nicht funktioniert](#9-wenn-etwas-nicht-funktioniert)

---

## 1. Überblick

### 1.1 Was wo läuft

| Was | Wo es läuft | Seite | Daten |
| --- | --- | --- | --- |
| Box | ein Raspberry Pi im Holzgehäuse | `https://<Name der Box>.local`, z. B. `https://tinyjukebox.local` | `/srv/tinyjukebox/` auf ihrer SD-Karte |
| Bibliothek auf dem NAS | das Synology-Paket `tinyjukebox-library` | `https://<nas>:8866` | `/var/packages/tinyjukebox-library/var/` und der freigegebene Ordner `tinyjukebox` |
| Bibliothek in Docker oder auf einem PC | das Image `ghcr.io/tgd1975/tinyjukebox` oder `tinyjukebox-library --data ORDNER` | `https://<Rechner>:8866` | das Volume `/data` oder der Ordner `ORDNER` |
| App | ein Android-Telefon: ein Telefon als Box oder die Seite der Bibliothek | – | auf dem Telefon ([Benutzerhandbuch](manual.md#137-die-app-ein-telefon-als-box), Abschnitt 13.7) |

Eine Box arbeitet für sich allein. Mit einer Bibliothek teilen sich die Boxen Alben, Tags, Sender und Titelbilder,
und die Bibliothek merkt sich den Fortschritt jeder Box ([Benutzerhandbuch](manual.md#13-bibliothek), Abschnitt 13).
Eine Box ohne Bibliothek holt neue Versionen ihrer Software von GitHub, eine Box mit Bibliothek aus der Bibliothek.

### 1.2 Rollen

- `admin` darf alles: Alben, Tags, Sender, Benutzer, Backups, Versionen.
- `user` darf die Box bedienen – Status, Wiedergabe, Lautstärke, Tasten – und das eigene Passwort ändern; an der
  Bibliothek außerdem einen Tag durch Scannen bekannt machen.

Box und Bibliothek haben jede ihre eigenen Benutzer. Benutzer und Passwörter auf der Seite:
[Benutzerhandbuch](manual.md#11-benutzer-und-passwörter), Abschnitt 11.

### 1.3 Die Dateien einer Box

| Ordner | Inhalt |
| --- | --- |
| `/srv/tinyjukebox/albums/` | ein Ordner je Album: MP3-Dateien und `playlist.m3u` |
| `/srv/tinyjukebox/json/` | Tags, Alben, Sender, Fortschritte, Lautstärkegrenzen; `images/`: die Titelbilder |
| `/srv/tinyjukebox/ssl/` | Zertifikat der Seite und `users.yaml`, die Benutzer |
| `/srv/tinyjukebox/log/` | `tinyjukebox.log`, das Log der Box |
| `/srv/tinyjukebox/slots/`, `current` | die Versionen der Box-Software und die, die gerade läuft |
| `/srv/tinyjukebox/state/` | welche Version läuft und warum; `history.log`, eine Zeile je Wechsel |
| `/srv/tinyjukebox/identity/`, `sync/` | der Schlüssel der Box und die Adresse ihrer Bibliothek; was sie mit der Bibliothek austauscht |
| `/srv/tinyjukebox/spotify/` | Schlüssel, Player und Anmeldung von Spotify |
| `/srv/tinyjukebox/config/` | `tinyjukebox.yaml`, falls es sie gibt (Abschnitt 8) |

Alles gehört dem Benutzer der Box, auf unserer Box `pi`. Die vollständige Liste:
[box.md](../../developer/box.md#6-file-system-layout), Abschnitt 6.

---

## 2. Eine Box einrichten

### 2.1 Was man braucht

- einen Raspberry Pi mit der Hardware der Box ([box.md](../../developer/box.md#1-hardware), Abschnitt 1) – auch eine
  Box, die noch nicht verdrahtet ist, lässt sich installieren; ihre Seite antwortet, und das Log nennt, was fehlt
- eine SD-Karte und den Raspberry Pi Imager auf dem PC
- ein WLAN mit Internet: Der Installer holt die Pakete des Betriebssystems
- das Passwort, das der Benutzer `admin` der Seite der Box bekommen soll, mindestens 8 Zeichen
- mit Bibliothek: ihr Bootstrap-Passwort, festgelegt bei der Installation der Bibliothek (Abschnitt 3)

### 2.2 Die Karte schreiben

1. Im Raspberry Pi Imager „Raspberry Pi OS Lite (64 Bit)“ wählen.
2. In den Einstellungen des Imagers den Hostnamen setzen (z. B. `kueche` – die Seite ist dann `https://kueche.local`),
   den Benutzer, das WLAN mit seinem Land, die Zeitzone und SSH; der Imager bietet an, nur die Anmeldung mit
   Schlüssel zu erlauben.
3. Die Karte schreiben, in die Box stecken und die Box einschalten.
4. Vom PC aus anmelden: `ssh <Benutzer>@<Hostname>.local`.

Was der Imager eingestellt hat, fasst der Installer nie an: Hostname, Benutzer, WLAN, Zeitzone, SSH.

### 2.3 Der Installer

Eine Box, die für sich allein arbeitet, holt den Installer von GitHub:

```bash
curl -fsSL https://github.com/tgd1975/tinyjukebox/releases/latest/download/install.sh | sudo bash
```

Eine Box für eine Bibliothek bekommt ihre Zeile von der Seite der Bibliothek: Tab „Boxen“, Abschnitt „Box
hinzufügen“, „Kopieren“. Die Zeile nennt die Bibliothek und den Schlüssel ihres Zertifikats, damit die Box genau
dieser Bibliothek vertraut.

Was passiert:

- **Alle Fragen kommen zuerst:** das Passwort des Benutzers `admin` der Seite und, mit Bibliothek, das
  Bootstrap-Passwort. Sie werden vom Terminal gelesen, nie von der Befehlszeile.
- Der Installer prüft, ob er auf Raspberry Pi OS Lite (64 Bit), Debian 13, läuft. Auf allem anderen hört er auf,
  bevor er etwas ändert, und sagt, was er gefunden hat.
- Er richtet das System ein, installiert die Box-Software in einen Slot und zeigt die Adresse der Seite. Er startet
  die Box einmal neu. Auf einem Pi 3 dauert das ein paar Minuten; kompiliert wird nichts.
- Eine Box für eine Bibliothek meldet sich bei ihrem ersten Start an und erscheint von selbst im Tab „Boxen“ der
  Bibliothek.
- Was er ausgegeben hat, steht in `/var/log/tinyjukebox-install.log`, auch was nach dem Neustart geschah.

**Ein zweiter Lauf** schadet nicht: Er ändert nichts, was schon stimmt, und er fasst Alben, Tags, Fortschritte,
Benutzer und die Verbindung zu einer Bibliothek nie an. Er ist auch der Weg, das System einer Box auf den neuen Stand
zu bringen (Abschnitt 4.4). Ein Deinstallationsprogramm gibt es nicht: Einen Pi macht man frei, indem man seine Karte
neu schreibt.

Alle Optionen des Installers: [deployment.md](../../developer/deployment.md#51-with-the-installer), Abschnitt 5.1.

### 2.4 Nach der Installation

- Der erste Start sollte mit WLAN geschehen: Die Box hat keine Uhr, und das Zertifikat der Seite entsteht dabei.
- Die Seite öffnen und als `admin` anmelden. Beim ersten Mal warnt der Browser vor dem Zertifikat (Abschnitt 5.4).
- Die Sprache unter der Anmeldung wählen, „Deutsch / English“.
- Einen Tag auflegen: Die LED wird gelb, und der Tab „Jetzt“ bietet an, ihn zu verknüpfen
  ([Benutzerhandbuch](manual.md#72-einen-tag-verknüpfen), Abschnitt 7.2).
- Gibt es mehrere Boxen, der Box einen Namen geben: Tab „System“, Abschnitt „Bibliothek“, „Umbenennen“.

Für eine neu eingerichtete Box geht die [Abnahme-Checkliste](../../developer/deployment.md#8-acceptance-checklist)
Start, Tasten, LED, Tags und Kopfhörer der Reihe nach durch.

### 2.5 Mit dem Playbook

Unsere eigene Box wird vom PC aus mit einem Ansible-Playbook eingerichtet. Es führt den Installer aus und schreibt
außerdem die Konfiguration der Box und ihre Benutzer. Das ist der Weg für Entwickler, die die Box im Repository
führen: [deployment.md](../../developer/deployment.md#52-with-the-playbook), Abschnitt 5.2.

---

## 3. Die Bibliothek

### 3.1 Wo sie läuft

| Wo | Wie | Aktualisiert |
| --- | --- | --- |
| Synology-NAS | das Paket `tinyjukebox-library`, installiert im Paket-Zentrum | von Hand, mit einem neueren Paket (Abschnitt 4.3) |
| Docker | das Image `ghcr.io/tgd1975/tinyjukebox` (`linux/amd64`, `linux/arm64`) | durch Holen eines neueren Images |
| PC | `pip install tinyjukebox`, dann `tinyjukebox-library --data ORDNER` | mit `pip install --upgrade tinyjukebox` |

Das Paket ist für eine Plattform gebaut, `rtd1296` – die DS418j und NAS mit demselben Prozessor. Ein anderes NAS
braucht einen anderen Build oder Docker.

### 3.2 Auf dem NAS

1. **Vorher:** das Python 3.12 von SynoCommunity auf dem NAS installieren (das Paket `python312`). Ein
   Bootstrap-Passwort mit mindestens 16 Zeichen wählen, z. B. mit `openssl rand -hex 24`. Jede Box weist es einmal
   vor, beim ersten Verbinden.
2. **Das Paket holen** (`tinyjukebox-library-<version>-<build>-rtd1296.spk`), aus dem neuesten Release auf GitHub. Mit
   einem Klon des Repositorys auf dem PC holt `./fetch_release.sh` es nach `dist/` und prüft es.
3. **Installieren:** Paket-Zentrum von DSM, „Manuelle Installation“, die Datei wählen, die Warnung vor Paketen von
   Drittanbietern bestätigen. Der Dialog fragt nach dem Passwort des Benutzers `admin` der Seite (mindestens
   8 Zeichen) und nach dem Bootstrap-Passwort.
4. **Prüfen:** Das Paket-Zentrum zeigt das Paket als laufend, und `https://<nas>:8866` öffnet die Seite. Als `admin`
   anmelden. Ist der Start fehlgeschlagen, steht der Grund in `var/server.log` (Abschnitt 7.2).
5. **Benutzer:** Die Bibliothek hat nur den Benutzer `admin`. Die anderen auf ihrer Seite anlegen, Tab „System“,
   Abschnitt „Benutzer“.

Das Paket läuft als eigener Benutzer, `tinyjukebox-library`. DSM legt beim ersten Start den freigegebenen Ordner
`tinyjukebox` an, darin `albums/`, `images/` und `json/`; Alben lassen sich auch per SMB oder File Station dort
ablegen, und Hyper Backup sichert den Ordner mit. Zertifikat, Benutzer und Konfiguration liegen in
`/var/packages/tinyjukebox-library/var/`. Ein Update behält diesen Ordner, und DSM löscht ihn nie – auch nicht, wenn
das Paket entfernt wird. Ganz von vorn beginnen:
[library.md](../../developer/library.md#37-first-installation-on-the-nas), Abschnitt 3.7.

### 3.3 In Docker

Zwei Passwörter als Dateien, dann startet compose eine Bibliothek und eine Box ohne Hardware zum Ausprobieren:

```bash
cd deploy/docker && openssl rand -hex 12 > admin-password && openssl rand -hex 24 > bootstrap-password
docker compose up -d   # https://localhost:8866/ und https://localhost:8443/, Benutzer admin, das Passwort in admin-password
```

Die Bibliothek allein:

```bash
docker run -d --name library --restart unless-stopped -p 8866:8866 \
    -v library:/data -v /pfad/zu/alben:/data/albums ghcr.io/tgd1975/tinyjukebox
```

- Alles, was bleiben muss, liegt unter `/data`: ein Volume, und wenn gewünscht die Alben als Ordner des Rechners. Ein
  eingebundener Ordner muss für den Benutzer des Images (uid 1000) beschreibbar sein.
- Ohne `TINYJUKEBOX_ADMIN_PASSWORD` wird das Passwort von `admin` erzeugt und einmal ausgegeben: `docker logs library`.
- Das Bootstrap-Passwort kommt aus `TINYJUKEBOX_BOOTSTRAP_PASSWORD`, oder es wird im Tab „Boxen“ gesetzt.
- Statt der Passwörter selbst nennen `TINYJUKEBOX_ADMIN_PASSWORD_FILE` und `TINYJUKEBOX_BOOTSTRAP_PASSWORD_FILE` eine
  Datei, die sie enthält; eine Variable lässt sich mit `docker inspect` lesen.
- Eine Box kommt mit der Zeile des Tabs „Boxen“ dazu. Ihre Adresse muss die des Rechners sein, nicht der Name des
  Containers.

Mehr: [library.md](../../developer/library.md#39-the-library-in-docker-and-with-one-folder), Abschnitt 3.9.

### 3.4 Auf einem PC

```bash
pip install tinyjukebox
tinyjukebox-library --data ~/bibliothek        # https://localhost:8866/
```

Alles liegt unter dem Ordner: `data/`, `albums/`, `images/`, `ssl/`, `log/`, `backups/`. Beim ersten Start fragt sie
im Terminal nach dem Passwort von `admin`. Ein ausgeschalteter Rechner ist für die Boxen keine Bibliothek: Sie spielen
mit dem weiter, was sie haben, und liefern ihre Änderungen später ab.

### 3.5 Mit den Daten einer Box füllen

Die Bibliothek beginnt leer. Hat eine Box schon Alben und Tags, bringt ein Befehl auf dem PC sie hinüber. Er behält
die Nummern, damit Box und Bibliothek von Anfang an übereinstimmen, und er lässt sich wiederholen: Was schon da ist,
wird übersprungen.

```bash
rsync -a pi@tinyjukebox.local:/srv/tinyjukebox/json/ ~/box-copy/json/
rsync -a pi@tinyjukebox.local:/srv/tinyjukebox/albums/ ~/box-copy/albums/
tinyjukebox-fill-library https://<nas>:8866 --user admin \
    --json ~/box-copy/json --albums ~/box-copy/albums --images ~/box-copy/json/images
```

Er fragt nach dem Passwort und zeigt den Fingerabdruck des Zertifikats der Bibliothek zur Bestätigung (Abschnitt 5.4).
In einem Klon des Repositorys heißt der Befehl `uv run tinyjukebox-fill-library …`. Die Fortschritte kommen mit der
ersten Synchronisation von der Box selbst. Eine Box, die man einfach mit der Bibliothek verbindet, bringt ihre Daten
ebenfalls mit ([Benutzerhandbuch](manual.md#132-eine-box-mit-der-bibliothek-verbinden), Abschnitt 13.2); dieser
Befehl ist für viele Alben auf einmal.

### 3.6 Die Boxen verbinden

- **Eine Box, die schon läuft:** auf ihrer Seite, Tab „System“, Abschnitt „Bibliothek“: Adresse und
  Bootstrap-Passwort ([Benutzerhandbuch](manual.md#132-eine-box-mit-der-bibliothek-verbinden), Abschnitt 13.2).
- **Eine neue Box:** die Zeile des Tabs „Boxen“, Abschnitt „Box hinzufügen“ (Abschnitt 2.3).
- **Eine neu eingerichtete Box** ist eine neue Box. Auf der Seite der Bibliothek, Tab „Boxen“, übernimmt sie die
  Fortschritte und Einstellungen der alten.

### 3.7 Neue Versionen für die Boxen

Die Bibliothek holt die Versionen der Box-Software aus den Releases auf GitHub und gibt sie an ihre Boxen weiter.
Dafür braucht sie Zugang zu den Releases:

- **Das Repository ist öffentlich:** Eine leere Datei `var/github-token` auf dem NAS genügt, mit dem Benutzer
  `tinyjukebox-library` als Eigentümer; danach das Paket stoppen und starten.
- **Das Repository ist privat:** auf GitHub einen fein granulierten Token für dieses eine Repository mit dem Recht
  „Contents: Read-only“ anlegen und auf der Seite der Bibliothek eintragen, Tab „Versionen“, Abschnitt „Zugang zu den
  Releases“. Die Bibliothek prüft ihn, bevor sie ihn speichert, und sucht sofort nach einem Release.

Das Docker-Image und `tinyjukebox-library --data` fragen die Releases des Projekts ohne Token. Welche Version eine Box
bekommt und wie sie ausprobiert wird: Abschnitt 4.1.

---

## 4. Updates

### 4.1 Die Box-Software

Eine Box installiert neue Versionen selbst: mit Bibliothek aus der Bibliothek, ohne von GitHub. Eine neue Version
startet beim nächsten Einschalten der Box, auf Probe. Funktioniert sie nicht – startet sie nicht oder scheitert sie in
den ersten Sekunden –, startet die Box nach wenigen Sekunden von selbst die Version davor. Auf Probe bleibt eine
Version, bis ein Tag aufgelegt wurde und deswegen ein Album gespielt hat.

- **Ohne Bibliothek** sieht die Box einmal am Tag nach einem neuen Release und installiert es nach einer Wartezeit
  von drei Tagen; eine Korrektur in dieser Zeit ersetzt es, bevor irgendeine Box es bekommen hat.
- **Mit Bibliothek** bekommt eine Box eine neue Version sofort. Im Tab „Versionen“ der Bibliothek hat jede Version
  einen Zustand: `use` (Boxen installieren sie von selbst), `manual` (nur von Hand), `dontuse` (jede Box verlässt sie
  sofort).
- **Die Update-Art** einer Box – „automatisch“, „nur von Hand“ („nachfragen“ ohne Bibliothek) oder „aus“ – wird auf
  ihrer Seite eingestellt, Tab „System“, Abschnitt „Version“ ([Benutzerhandbuch](manual.md#134-version-der-box),
  Abschnitt 13.4).

Auf der Box ist dasselbe ein Befehl. Er läuft als Benutzer der Box, nie mit `sudo`:

```bash
tinyjukebox-slot status           # die Versionen auf der Box, die laufende, die Update-Art, die letzten Wechsel
tinyjukebox-slot check            # was die Bibliothek anbietet - ohne Bibliothek: GitHub
tinyjukebox-slot update --now     # die neueste erlaubte Version, sofort
tinyjukebox-slot update 0.45.0    # genau diese Version
tinyjukebox-slot mode manual      # automatic, manual oder off
```

Jeder Aufruf: [library.md](../../developer/library.md#68-the-command-tinyjukebox-slot), Abschnitt 6.8.

### 4.2 Zurück zur Version davor

Ein Schritt zurück wirkt sofort, auch wenn gerade etwas spielt. Die verlassene Version installiert danach keine Box
mehr von selbst.

| Weg | Für |
| --- | --- |
| Seite der Box, Tab „System“, „Zurück zur vorigen Version“ | diese Box |
| `tinyjukebox-slot revert` auf der Box | diese Box |
| eine leere Datei `tinyjukebox-revert` auf der Boot-Partition der SD-Karte (`bootfs` am PC) | diese Box, ohne Netz: Der nächste Start geht zurück und schreibt an ihre Stelle `tinyjukebox-ergebnis.txt` |
| Seite der Bibliothek, Tab „Versionen“: die Version auf `dontuse` | alle Boxen |

`revert` wechselt in den anderen Slot, egal was darin liegt. Nach einer gescheiterten Probe kann das die gescheiterte
Version sein: vorher mit `tinyjukebox-slot status` nachsehen. Startet keine Version mehr, ist die Box im Notfall-Modus
([Benutzerhandbuch](manual.md#134-version-der-box), Abschnitt 13.4;
[library.md](../../developer/library.md#610-fallback-slot-and-recovery-mode), Abschnitt 6.10).

### 4.3 Die Bibliothek

Die Bibliothek aktualisiert sich nie selbst.

- **Auf dem NAS:** das neuere Paket genauso installieren, Paket-Zentrum, „Manuelle Installation“ – ohne Dialog; die
  Daten bleiben. Aus einem Klon des Repositorys auf dem PC erledigt das `./update_library.sh <nas>`: Es holt das Paket
  des neuesten Releases und fragt nach Benutzer und Passwort von DSM, die es nicht speichert.
- **In Docker:** Die Seite zeigt, dass es eine neuere Version gibt. Das neuere Image holen und den Container neu
  starten; mit compose `docker compose pull`, dann `docker compose up -d`.
- **Auf einem PC:** `pip install --upgrade tinyjukebox`, dann die Bibliothek neu starten.

Eine Bibliothek, die älter ist als die Version einer Box, hält die Box an: Die LED wechselt zwischen rot, weiß,
magenta und gelb, und die Box spielt nichts. Dann zuerst die Bibliothek aktualisieren.

### 4.4 Das Betriebssystem der Box

Ihr Betriebssystem aktualisiert die Box nie selbst. Von Hand, ab und zu:

```bash
sudo apt update && sudo apt full-upgrade
```

Nach einem Upgrade von Python, ALSA oder BlueZ die Zeile des Installers noch einmal ausführen (Abschnitt 2.3) – auf
einer Box des Playbooks `./update_box.sh` – und Ton, Tasten und Kopfhörer prüfen. Braucht eine Version ein neueres
System, sagt sie das auf der Seite der Box: Dann muss der Installer ebenfalls noch einmal laufen.

---

## 5. Benutzer und Zugang

### 5.1 Benutzer auf der Befehlszeile

Auf der Seite legt ein Admin Benutzer an, löscht sie und setzt Passwörter
([Benutzerhandbuch](manual.md#11-benutzer-und-passwörter), Abschnitt 11). Auf der Box, als Benutzer der Box:

```bash
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml list
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add NAME --role admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd NAME
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml role NAME admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml remove NAME
```

- Nur hier lässt sich eine Rolle ändern. Den letzten Admin löscht der Befehl nicht und macht ihn auch nicht zum `user`.
- Eine Änderung wirkt sofort, ohne Neustart. Ein neues Passwort beendet die anderen Sitzungen dieses Benutzers.
- Für eine Bibliothek auf einem PC ist die Datei `ssl/users.yaml` in ihrem Ordner. In Docker führt das Image den
  Befehl aus, mit dem Volume der Bibliothek: `docker run --rm -v library:/data ghcr.io/tgd1975/tinyjukebox
  tinyjukebox-users -f /data/ssl/users.yaml list`.
- Das Paket auf dem NAS hat keinen Befehl `tinyjukebox-users`: Der Aufruf mit dem Python des Pakets steht in
  [operations.md](../../developer/operations.md#4-users-of-the-web-page), Abschnitt 4.

### 5.2 Ein vergessenes Passwort

1. Ein anderer Admin setzt auf der Seite ein neues Passwort, Tab „System“, Abschnitt „Benutzer“.
2. Kennt kein Admin mehr sein Passwort: sich mit SSH auf der Box anmelden.
3. `tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd admin` – fragt zweimal nach dem neuen Passwort.
4. Damit auf der Seite anmelden.

### 5.3 SSH

Erreichbar ist die Box mit `ssh <Benutzer>@<Hostname>.local`, so wie der Imager sie eingerichtet hat. Wird der Name
nicht gefunden, hilft die Adresse der Box im Router. Eine neu geschriebene Karte hat einen neuen Schlüssel: Kennt der
PC noch den alten, verweigert SSH die Verbindung, bis der alte Eintrag mit `ssh-keygen -R <Hostname>.local` entfernt
ist.

### 5.4 Das Zertifikat der Seite

Box und Bibliothek erzeugen ihr eigenes Zertifikat, bei ihrem ersten Start. Beim ersten Mal warnt der Browser davor.

- Den Fingerabdruck, den der Browser zeigt, mit dem im Log vergleichen: Die Box schreibt ihn, wenn die Seite startet
  (`rest api listening on … fingerprint`), die Bibliothek ebenfalls in ihr Log.
- Oder das Zertifikat der Box als vertrauenswürdig importieren: `/srv/tinyjukebox/ssl/tinyjukebox.crt`.
- Die Befehle auf dem PC, `tinyjukebox-backup` und `tinyjukebox-fill-library`, zeigen den Fingerabdruck ebenfalls
  und fragen, bevor sie weitermachen.

---

## 6. Daten und Sicherungen

### 6.1 Was ein Backup enthält

Ein Backup ist eine Datei mit den Daten einer Box oder der Bibliothek, erstellt im laufenden Betrieb:

- eine Box: Tags, Alben, Sender und Spotify-Einträge, Fortschritte, Lautstärkegrenzen und die Titelbilder
- die Bibliothek: der Katalog, Fortschritte und Einstellungen jeder Box, die Statistik, die Zustände der Versionen und
  die Titelbilder
- mit „MP3s“ auch die Alben – die Datei ist dann so groß wie alle Alben zusammen

Eine Box mit Bibliothek braucht kein eigenes Backup: Die Bibliothek hat alles, und eine neu eingerichtete Box holt es
sich von dort.

### 6.2 Backups erstellen

| Weg | Box | Bibliothek |
| --- | --- | --- |
| Seite, Tab „System“, Karte „Backup“ | „Backup herunterladen“, „Backup mit MP3s herunterladen“ | ebenso |
| Seite, „Regelmäßig sichern“ | eine Box ohne Bibliothek, nach `/srv/tinyjukebox/backups` | in den freigegebenen Ordner `tinyjukebox` auf dem NAS, den Hyper Backup mitsichert |
| Befehl auf dem PC | `tinyjukebox-backup https://tinyjukebox.local --user admin [--albums] [ORDNER]` | `tinyjukebox-backup https://<nas>:8866 --user admin [--albums] [ORDNER]` |

Zwei Zeitpläne, einer ohne und einer mit den Alben, jeder mit seinem Ordner und der Zahl der Backups, die bleiben:
[Benutzerhandbuch](manual.md#112-regelmäßig-sichern), Abschnitt 11.2. Die Backups einer Box liegen auf ihrer
SD-Karte: Gegen eine kaputte Karte hilft nur ein Backup, das man von ihr geholt hat.

### 6.3 Ein Backup zurückspielen

Auf der Seite, Tab „System“, Karte „Backup“, „Backup zurückspielen“ – oder `tinyjukebox-backup … --restore DATEI` auf
dem PC. Die Seite fragt einmal nach, dann startet die Box oder die Bibliothek mit den Daten des Backups neu
([Benutzerhandbuch](manual.md#113-backup-zurückspielen), Abschnitt 11.3).

- Ein Backup ohne die Alben lässt den Ordner der Alben in Ruhe. Alben, die in den Daten stehen, deren Ordner aber
  fehlt, werden nach dem Zurückspielen aufgelistet.
- Auf einer Box mit Bibliothek gewinnt bei der nächsten Synchronisation die Bibliothek. Zurückspielen ist für eine Box
  ohne Bibliothek und für die Bibliothek gedacht.
- Die Bibliothek auf einem anderen NAS, oder von Hand ohne das Programm:
  [operations.md](../../developer/operations.md#103-putting-a-backup-back), Abschnitt 10.3.

### 6.4 Was in keinem Backup ist

- Benutzer, Passwörter, das Zertifikat, Tokens – in der Bibliothek auch `settings.json` mit dem Bootstrap-Passwort
- der Schlüssel der Box und ihre Verbindung zur Bibliothek
- Schlüssel, Player und Anmeldung von Spotify: Der Schlüssel wird neu eingefügt, die Box holt den Player neu und wird
  neu verbunden („Verbinden“)
- die Konfigurationsdatei

---

## 7. Logs und Diagnose

### 7.1 Die Logs einer Box

| Log | Inhalt |
| --- | --- |
| Seite, Tab „Log“ (admin) | die letzten 5000 Einträge, live, mit Filtern für Stufe und Text |
| `/srv/tinyjukebox/log/tinyjukebox.log` (+ `.1` … `.4`) | das vollständige Log; es bleibt über einen Neustart |
| `journalctl -u tinyjukebox` | Warnungen und Fehler, und was das Start-Skript sagt; nach einem Neustart weg |
| `/srv/tinyjukebox/state/history.log` | eine Zeile je Wechsel der Version: wann, von, nach, warum, durch wen |
| `/var/log/tinyjukebox-install.log` | was der Installer getan hat |

Der Dienst selbst: `sudo systemctl status tinyjukebox`, `sudo systemctl restart tinyjukebox`. Nach einem Absturz
startet systemd ihn von selbst neu.

### 7.2 Die Logs der Bibliothek

| Log | Inhalt |
| --- | --- |
| Seite der Bibliothek, Tab „Log“ | das Log der Bibliothek und das jeder Box: Warnungen, Fehler und Ereignisse, die die Boxen geschickt haben, mit Filtern; „Als Datei speichern“ |
| NAS: `/var/packages/tinyjukebox-library/var/library.log` | das Log der Bibliothek; beim Start ihr Fingerabdruck |
| NAS: `var/server.log` | warum ein Start fehlgeschlagen ist |
| NAS: `/var/log/packages/tinyjukebox-library.log` | was die Skripte des Pakets ausgegeben haben |
| Docker: `docker logs library` | die Ausgabe der Bibliothek, das erzeugte Passwort von `admin` |

Der Tab „Log“ der Bibliothek hilft, wenn eine Box nicht zur Hand ist: Die Boxen schicken ihre Warnungen und Fehler an
die Bibliothek, ohne Passwörter, Schlüssel oder UIDs von Tags. Eine Box, die nicht starten kann, meldet auch das; der
Tab „Boxen“ zeigt sie dann mit „startet seit … nicht“
([library.md](../../developer/library.md#422-central-diagnostics), Abschnitt 4.22).

### 7.3 Mehr Details im Log

Die Box schreibt ins Log auf der Stufe INFO. Für eine Fehlersuche auf DEBUG stellen:

1. Eine Box des Installers hat keine eigene Konfigurationsdatei. Zuerst die ihres Slots kopieren:
   `cp /srv/tinyjukebox/current/config/tinyjukebox.yaml /srv/tinyjukebox/config/`.
2. In `/srv/tinyjukebox/config/tinyjukebox.yaml` `log-level: DEBUG` setzen.
3. `sudo systemctl restart tinyjukebox`.
4. Nach der Fehlersuche die Kopie wieder entfernen und den Dienst neu starten: Sonst wäre die Datei ab da die
   Konfiguration jeder Version, auch jeder späteren.

### 7.4 Tasten, LED und Leser

Der Tab „System“ hat für Admins die Karte „Hardware“: was die Treiber über die Tasten, die LED und den RFID-Leser
sagen, zweimal pro Sekunde. Hat eine Taste keine Wirkung, zeigt sie, ob die Taste überhaupt an der Box ankommt. Die
Hardware von Hand auf der Konsole prüfen:
[operations.md](../../developer/operations.md#92-check-the-hardware-by-hand), Abschnitt 9.2.

---

## 8. Konfiguration

### 8.1 Wo die Datei liegt

| Box oder Bibliothek | Konfiguration |
| --- | --- |
| Box des Installers | die Konfiguration ihres Slots, `/srv/tinyjukebox/current/config/tinyjukebox.yaml`; sie kommt mit jeder Version |
| Box des Playbooks | `/srv/tinyjukebox/config/tinyjukebox.yaml`, geschrieben bei jedem Lauf des Playbooks |
| Bibliothek auf dem NAS | `/var/packages/tinyjukebox-library/var/library.yaml` |
| `--data ORDNER` (Docker, PC) | eingebaut; `ORDNER/config.yaml`, falls es sie gibt, ändert einzelne Werte |

Das meiste, was ein Admin einstellt, steht auf der Seite und braucht keine Datei: Lautstärkegrenzen
([Benutzerhandbuch](manual.md#10-lautstärke-begrenzen), Abschnitt 10), die Bibliothek einer Box, Update-Art, Backups.
Eine Änderung der Datei wirkt nach `sudo systemctl restart tinyjukebox`.

### 8.2 Schlüssel, die ein Admin ändert

| Schlüssel | Bedeutung | Auf der Box |
| --- | --- | --- |
| `logger/log-level` | wie viel das Log sagt | `INFO` |
| `tinyjukebox/rest-api/session-timeout` | Abmeldung nach so vielen Sekunden ohne Anfrage | `604800` (7 Tage) |
| `tinyjukebox/rest-api/idle-timeout` | Abmeldung nach so vielen Sekunden, in denen auf einer offenen Seite nichts bedient wurde; `0`: nie | `1800` (30 Minuten) |
| `tinyjukebox/rest-api/max-upload-size` | MB je hochgeladener MP3-Datei | `500` |
| `tinyjukebox/sync/keep-free` | MB, die auf der Karte einer Box mit Bibliothek frei bleiben; ist weniger frei, gehen die Alben, die am längsten nicht gespielt wurden | `500` |
| `tinyjukebox/backups-folder` | wo eine Box ohne Bibliothek ihre Backups ablegt, bis auf ihrer Seite ein anderer Ordner gesetzt ist | `backups` neben den Alben |

Jeder Schlüssel: [configuration.md](../../developer/configuration.md#4-section-tinyjukebox), Abschnitte 4 bis 6, und
auf der Dokumentations-Seite die erzeugte Tabelle aller Schlüssel (Tab „Reference“, „Configuration keys“). Unbekannte
Schlüssel verhindern den Start: Die Meldung nennt den Pfad des Schlüssels.

---

## 9. Wenn etwas nicht funktioniert

Das Benutzerhandbuch nennt, was ein Benutzer tun kann
([Benutzerhandbuch](manual.md#12-wenn-etwas-nicht-funktioniert), Abschnitt 12). Für einen Admin:

| Symptom | Nachsehen | Ursache und Abhilfe |
| --- | --- | --- |
| LED bleibt rot | `sudo systemctl status tinyjukebox`, `journalctl -u tinyjukebox` | der Dienst läuft nicht oder scheitert beim Start, z. B. an einer ungültigen Konfiguration: die Meldung nennt den Pfad |
| Kein Tag wird erkannt | das Log: `RFID - …` | die Zeile sagt, warum: Der Leser lässt sich nicht starten, er antwortet nicht (Verdrahtung, Versorgung), oder dem Programm der Version wird nicht vertraut – dann den Installer noch einmal ausführen |
| Webseite nicht erreichbar | `sudo systemctl status tinyjukebox` | der Dienst läuft nicht; oder der Name `….local` wird nicht gefunden: die Adresse der Box verwenden |
| LED abwechselnd rot, weiß, magenta, gelb; nichts spielt | Tab „System“; `tinyjukebox-slot status` | die Bibliothek ist zu alt für die Version der Box: die Bibliothek aktualisieren (Abschnitt 4.3) oder an der Box zurückgehen (Abschnitt 4.2) |
| Die Box bekommt keine neue Version | `tinyjukebox-slot status`, `tinyjukebox-slot check`; Tab „Versionen“ der Bibliothek | Update-Art „nur von Hand“ oder „aus“; die Version ist nicht `use`; die Box ist schon einmal von ihr zurückgegangen; die Bibliothek hat keinen Zugang zu den Releases (Abschnitt 3.7) |
| Die Box läuft wieder mit einer älteren Version | `/srv/tinyjukebox/state/history.log`, Tab „Boxen“ der Bibliothek | die neue Version hat ihre Probe nicht bestanden, und die Box ist zurückgegangen; die Zeile sagt, warum |
| Die Seite der Box sagt, die Bibliothek sei nicht verbunden | Tab „System“, Abschnitt „Bibliothek“; das Log | das NAS ist aus oder nicht in diesem WLAN: Die Box spielt, was sie hat, und liefert ihre Änderungen später ab |
| Die Bibliothek startet nicht | `var/server.log` auf dem NAS; `docker logs library` | die Zeile sagt, warum, z. B. fehlt `var/library.yaml` |

Weitere Symptome, mit den Zeilen des Logs: [operations.md](../../developer/operations.md#93-symptoms), Abschnitt 9.3.
