# Tiny Jukebox – Benutzerhandbuch

Tiny Jukebox ist eine Musikbox für Kinder. Einen Tag auf die Box legen, und das Album des Tags spielt an der Stelle
weiter, an der es zuletzt aufgehört hat. Bedient wird die Box mit vier Tasten; eine LED zeigt ihren Zustand. Alben,
Tags und Radiosender verwaltet man auf einer Webseite. Gibt es mehrere Boxen oder eine Bibliothek im Heimnetz,
teilen sich die Boxen Alben, Tags und Sender (Abschnitt 11).

Das Wichtigste auf einer Karte: [Kurzanleitung](quick-guide.md). Technik, Einrichtung und Fehlersuche für
Administratoren: [`docs/developer/`](../../developer/operations.md) (englisch).

Inhalt:

1. [Begriffe](#1-begriffe)
2. [Die Box bedienen](#2-die-box-bedienen)
3. [LED](#3-led)
4. [Pause](#4-pause)
5. [Bluetooth-Kopfhörer](#5-bluetooth-kopfhörer)
6. [Webseite](#6-webseite)
7. [Alben, Tags und Radiosender verwalten](#7-alben-tags-und-radiosender-verwalten)
8. [Lautstärke begrenzen](#8-lautstärke-begrenzen)
9. [Benutzer und Passwörter](#9-benutzer-und-passwörter)
10. [Wenn etwas nicht funktioniert](#10-wenn-etwas-nicht-funktioniert)
11. [Bibliothek](#11-bibliothek)

---

## 1. Begriffe

| Begriff | Bedeutung |
| --- | --- |
| Tag | was auf die Box gelegt wird: eine Scheibe, eine Figur, eine Karte – alles mit einem RFID-Chip. Die Box erkennt den Chip an seiner eindeutigen Nummer (UID), z. B. `04:1A:2B:3C:4D:5E:6F`, nicht den Gegenstand. |
| Album | ein Ordner mit MP3-Dateien und einer Titelliste (Playlist) |
| Radiosender | ein Internet-Radiostream; braucht WLAN |
| Verknüpfen | einem Tag ein Album oder einen Radiosender zuordnen |
| Titelbild | das Bild eines Albums oder Radiosenders. Ein Tag zeigt das Titelbild seines Albums oder Senders. |
| Fortschritt | die gespeicherte Stelle eines Albums, je Tag |
| Bibliothek | ein Programm im Heimnetz (z. B. auf dem NAS), das Alben, Tags und Sender für alle Boxen hält |

---

## 2. Die Box bedienen

### 2.1 Ein- und Ausschalten

- **Einschalten:** Strom anschließen. Während des Starts leuchtet die LED rot, dann magenta. Leuchtet sie grün, ist
  die Box bereit.
- **Ausschalten:** Strom trennen. Das ist jederzeit erlaubt, auch während ein Album läuft. Die Box speichert die
  Stelle alle 5 Sekunden; beim nächsten Mal geht es höchstens wenige Sekunden früher weiter.
- Die Box spielt auch ohne WLAN. Nur die Webseite und Radiosender brauchen es.

### 2.2 Tags

| Aktion | Wirkung |
| --- | --- |
| Tag auf die Box legen | Das Album spielt an der gespeicherten Stelle weiter. Ein Radiosender beginnt zu spielen. |
| Tag abnehmen | Die Wiedergabe stoppt, die Stelle wird gespeichert. |
| anderen Tag auflegen | Deren Album spielt. |
| neuen, unbekannten Tag auflegen | Die LED leuchtet gelb. Die Box merkt sich den Tag; verknüpft wird er auf der Webseite (Abschnitt 7.2). |

Jeder Tag hat seinen eigenen Fortschritt. Ein Album, das zu Ende gehört wurde, beginnt beim nächsten Auflegen von
vorn.

### 2.3 Tasten

| Taste | kurz drücken | halten |
| --- | --- | --- |
| lauter, leiser | ein Schritt (5 %) | weiter in kleinen Schritten (1 % alle 0,2 s) |
| weiter | nächster Titel | – |
| zurück | an den Anfang des Titels; in den ersten 10 Sekunden eines Titels: zum vorigen Titel | 1 s: das Album beginnt von vorn |
| weiter + zurück zusammen | – | 5 s: Kopfhörer koppeln (Abschnitt 5.1) |

- Die Lautstärke ist begrenzt (Abschnitt 8).
- Ist das Album zu Ende, wirkt „weiter“ nicht. „zurück“ spielt den letzten Titel, „zurück“ 1 s halten das ganze
  Album von vorn.
- Bei einem Radiosender wirken „weiter“ und „zurück“ nicht. Den Sender wechselt man mit dem Tag.
- Jeder Tastendruck wirkt sofort. Beim Koppeln eines Kopfhörers springt ein laufendes Album deshalb um höchstens
  einen Titel. Wer das vermeiden will, nimmt den Tag vorher ab.

---

## 3. LED

| LED | Bedeutung |
| --- | --- |
| rot | Die Box startet gerade oder ist gestoppt. |
| magenta | Start oder Ende |
| grün | bereit, kein Tag aufgelegt |
| grün blinkend | seit 10 Minuten kein Tag – die Box ist noch eingeschaltet |
| aqua (türkis) | Das Album wird geladen, oder der Radiosender verbindet (auch nach einem Abbruch des Streams). |
| grün → aqua → blau, hin und her | Die Box holt das Album oder den nächsten Titel aus der Bibliothek und spielt, sobald er da ist (Abschnitt 11). |
| blau | spielt |
| blau blinkend | Das Album ist zu Ende. |
| weiß/blau im Wechsel | Pause (Abschnitt 4) |
| gelb | Der Tag ist mit keinem Album und keinem Sender verknüpft. |
| gelb/rot im Wechsel | Das verknüpfte Album oder der verknüpfte Sender existiert nicht mehr. |
| rot blinkend | Das Album kann nicht abgespielt werden (Ordner, Titelliste oder MP3-Dateien fehlen oder sind defekt). |
| blau schnell blinkend | Die Box sucht einen Kopfhörer (höchstens 60 s). |
| 2 s weiß | Kopfhörer gekoppelt – danach wieder die Farbe von vorher |
| 3 s rot schnell blinkend | kein Kopfhörer gefunden oder Koppeln fehlgeschlagen – danach wieder die Farbe von vorher |
| rot, weiß, magenta, gelb im Wechsel | Die Box ist außer Betrieb, weil die Bibliothek zu alt für sie ist. Das behebt nur ein Administrator (Abschnitt 11.4). |

Die Webseite zeigt dieselbe LED neben dem Logo.

---

## 4. Pause

In der Pause hält die Wiedergabe an, die Stelle wird gespeichert, und der Tag bleibt liegen. Die LED wechselt
zwischen weiß und blau.

Pausieren und fortsetzen:

- Play/Pause am Kopfhörer
- ⏸ / ▶ auf der Webseite (Tab „Jetzt“)
- Die Box selbst hat keine Pause-Taste. Tag abnehmen und später wieder auflegen hat dieselbe Wirkung.

| In der Pause | Wirkung |
| --- | --- |
| Play | läuft an derselben Stelle weiter |
| „weiter“ | 1. Druck: läuft an derselben Stelle weiter. Erst der 2. Druck springt zum nächsten Titel. |
| „zurück“ | springt sofort: an den Titelanfang, in den ersten 10 Sekunden zum vorigen Titel |
| „zurück“ 1 s halten | Das Album beginnt von vorn. |
| Tag abnehmen und wieder auflegen | läuft an der gespeicherten Stelle weiter |
| lauter, leiser | ändert nur die Lautstärke |

- Bei einem Album, das zu Ende ist, ändert Play/Pause nichts.
- Beim Radio beendet die Pause den Stream. Play, „weiter“ oder „zurück“ verbinden neu; das Radio läuft live weiter,
  eine Stelle gibt es nicht.
- Die Box pausiert von selbst, wenn die Verbindung zum Kopfhörer abbricht (Abschnitt 5.3).

---

## 5. Bluetooth-Kopfhörer

Die Box spielt über einen Bluetooth-Kopfhörer, sobald der gekoppelte Kopfhörer verbunden ist. Der Lautsprecher ist
dann stumm. Grundsätze:

- **Automatisch:** Ist der Kopfhörer verbunden, spielt er. Sonst spielt der Lautsprecher. Ein Menü gibt es nicht.
- **Nie überraschend laut:** Bricht die Verbindung ab, pausiert die Box. Sie wechselt nie von selbst auf den
  Lautsprecher.
- **Gehörschutz:** Der Kopfhörer hat eine eigene Startlautstärke und eine eigene Höchstlautstärke.
- **Genau ein Kopfhörer:** Es ist höchstens ein Kopfhörer gekoppelt. Ein neuer ersetzt den alten.

### 5.1 Koppeln

1. Den Kopfhörer in den Pairing-Modus versetzen. Wie das geht, steht in seiner Anleitung (Sony WH-CH530: die
   mittlere Taste etwa 5 s halten, bis er „Pairing“ sagt).
2. An der Box „weiter“ und „zurück“ zusammen 5 s halten. Danach können die Tasten losgelassen werden.
3. Die LED blinkt schnell blau, solange die Box sucht (höchstens 60 s).
4. Ergebnis:
   - 2 s weiß: Der Kopfhörer ist gekoppelt und verbunden.
   - 3 s schnell rot blinkend: nichts gefunden, oder das Koppeln ist fehlgeschlagen. Der bisherige Kopfhörer bleibt
     gekoppelt.

Weitere Regeln:

- Die Box sucht nur Audio-Geräte (Kopfhörer, Lautsprecher) im Pairing-Modus. Findet sie mehrere, nimmt sie das mit
  dem stärksten Signal – in der Regel das direkt neben der Box.
- Die Box selbst ist nie sichtbar. Ein fremdes Handy kann sich nicht mit ihr koppeln.
- Derselbe Kopfhörer lässt sich erneut koppeln, z. B. nach einem Werksreset des Kopfhörers.
- Die Suche lässt sich nicht abbrechen; sie endet nach 60 s von selbst. Wiedergabe und Tasten laufen währenddessen
  normal weiter.
- Koppeln und Kopplung aufheben geht auch auf der Webseite: Tab „Bluetooth“, Rolle `admin`.

### 5.2 Im Alltag

- **Kopfhörer einschalten genügt.** Die Box versucht alle 15 s, ihn zu erreichen.
- Verbindet er sich, während ein Album läuft, geht es auf dem Kopfhörer weiter (etwa 1 s zurück), und der
  Lautsprecher verstummt.
- Die Webseite zeigt neben der LED ein Kopfhörer-Symbol, solange er verbunden ist.
- Soll ein zweiter Kopfhörer benutzt werden, muss er neu gekoppelt werden. Der erste verbindet sich danach nicht
  mehr.

### 5.3 Verbindung weg

Akku leer, außer Reichweite, ausgeschaltet oder Kopplung aufgehoben:

| Situation | Verhalten |
| --- | --- |
| Kopfhörer weg, während ein Album läuft | Pause, die Stelle wird gespeichert. Der Lautsprecher bleibt still. |
| … der Kopfhörer kommt innerhalb von 10 Minuten zurück | Es läuft auf dem Kopfhörer weiter. |
| … der Kopfhörer kommt später zurück | Die Pause bleibt. Play am Kopfhörer setzt fort. |
| … „weiter“ oder „zurück“ an der Box | Es läuft auf dem Lautsprecher weiter. Die Tasten wirken wie in der Pause (Abschnitt 4). |
| … Tag abnehmen und wieder auflegen | Es läuft auf dem Lautsprecher weiter. |
| Kopfhörer weg, nichts läuft | Der Lautsprecher ist wieder aktiv. |

- War schon vor dem Abbruch pausiert, bleibt es auch nach dem Wiederverbinden pausiert.
- „lauter“ und „leiser“ an der Box ändern in dieser Pause nur die Lautstärke des Lautsprechers. Sie setzen nicht
  fort.

### 5.4 Tasten am Kopfhörer

| Taste am Kopfhörer | Wirkung |
| --- | --- |
| Weiter, Zurück | wie die Tasten der Box |
| Play/Pause | Pause und Fortsetzen (Abschnitt 4) |
| Lauter, Leiser | Lautstärke des Kopfhörers, begrenzt auf die Höchstlautstärke |

### 5.5 Lautstärke des Kopfhörers

| | Lautsprecher | Kopfhörer |
| --- | --- | --- |
| Startlautstärke | 50 %, beim Einschalten der Box | 40 %, bei jedem Verbinden |
| Höchstlautstärke | 82 % | 75 % |
| Tasten der Box ändern | den Lautsprecher | den Kopfhörer, solange er verbunden ist |

- Der Kopfhörer startet bei jedem Verbinden mit seiner Startlautstärke, nie mit der Lautstärke vom letzten Mal.
- Die Werte lassen sich ändern (Abschnitt 8).
- Die Prozentwerte beziehen sich auf den Kopfhörer: Wie laut 75 % sind, hängt vom Modell ab. Die Höchstlautstärke
  einmal mit dem Kopfhörer am Ohr einstellen.

### 5.6 Hinweise zu Kopfhörern

- **Handy in der Nähe:** Viele Kopfhörer halten zwei Verbindungen gleichzeitig (Multipoint) und spielen, was zuletzt
  Ton geschickt hat. Ist der Kopfhörer auch mit einem Handy verbunden, können dessen Töne die Box übertönen. Dann
  den Kopfhörer am Handy trennen.
- **Begrenzung in der App des Kopfhörers:** Manche Kopfhörer begrenzen die Lautstärke selbst (Sony: „Max Volume
  Control“ in der App „Sound Connect“). Das wirkt zusätzlich zur Grenze der Box. Ein Werksreset des Kopfhörers setzt
  es zurück.
- **LE Audio:** Die Box nutzt klassisches Bluetooth. Eine Einstellung wie „LE Audio Priority“ in der App des
  Kopfhörers muss ausgeschaltet sein.
- **Akkustand:** Die Webseite zeigt ihn im Tab „Bluetooth“, wenn der Kopfhörer ihn meldet.

---

## 6. Webseite

### 6.1 Öffnen und anmelden

1. Handy, Tablet oder PC muss im selben WLAN sein wie die Box.
2. `https://tinyjukebox.local` im Browser öffnen.
3. Der Browser warnt beim ersten Mal vor dem Zertifikat: Die Box stellt es sich selbst aus. Die Warnung bestätigen
   („Erweitert“ → „Weiter zu tinyjukebox.local“ oder ähnlich).
4. Sprache wählen: Die Seite ist beim ersten Mal Englisch. Die Auswahl „Deutsch / English“ steht unter der
   Anmeldung, nach dem Anmelden oben rechts und im Tab „System“.
5. Mit Benutzername und Passwort anmelden.

- Über der Anmeldung steht, wo man ist: „Bibliothek“, „Box“ oder die Box mit ihrem Namen, z. B. Box „antonia“.
  Jede Box hat ihre eigenen Farben; sie ergeben sich aus ihrem Namen (eine Box ohne Namen hat trotzdem feste
  Farben). So sieht man auch nach dem Anmelden, auf welcher Box man ist.
- Die Sprache merkt sich der Browser, für dieses Gerät und diese Box. Auf einem anderen Gerät und auf der Seite
  einer anderen Box oder der Bibliothek wird sie einmal neu gewählt.
- Die Anmeldung bleibt bestehen; nach 7 Tagen ohne Nutzung meldet die Seite ab.
- Nach 5 falschen Passwörtern sperrt jeder weitere Fehlversuch die Anmeldung von diesem Gerät für 30 Sekunden.

### 6.2 Rollen

| Rolle | darf |
| --- | --- |
| `user` | alles ansehen und die Box bedienen: Pause, Titel, Lautstärke, Tags auflegen; in der Bibliothek neue Tags mit dem Telefon scannen; das eigene Passwort ändern |
| `admin` | zusätzlich alles ändern: Alben, Tags, Radiosender, Titelbilder, Kopfhörer koppeln, Lautstärke-Grenzen, Benutzer |

Die Tabs „Hochladen“ und „Log“ und alle Schaltflächen zum Ändern sieht nur die Rolle `admin`.

### 6.3 Tabs

| Tab | Inhalt |
| --- | --- |
| Jetzt | was gerade läuft: Cover, Album, Titel, Stelle, Titelliste. Tasten zurück, Pause/Play, weiter, „Album von vorne“; Lautstärke mit Regler und Tasten. Liegt ein unbekannter Tag auf: Album wählen und „Verknüpfen & abspielen“. |
| Tags | der Tab für den Alltag: alle Tags mit dem Titelbild ihres Albums oder Senders, als Kacheln oder als Liste („Kacheln“ / „Liste“), alle oder nur die von Alben, von Radiosendern oder von Spotify („Alle“ / „Alben“ / „Radio“ / „Spotify“). Die Seite merkt sich die Auswahl. Ein Klick legt den Tag auf die Box, als läge er wirklich dort; ein zweiter Klick oder „Tag abnehmen“ nimmt ihn wieder ab. |
| Tag-Verwaltung | alle Tags mit Titelbild, Name, UID, Album oder Sender und Fortschritt. Hier werden Tags angelegt, benannt, verknüpft, gelöst und gelöscht. |
| Alben | eine Liste aller Alben zum Verwalten: Titelbild, Titel, Titelzahl, Spielzeit, Tags, der Tag, an dem sie hinzugefügt wurden, und der Ordner. Ein Klick auf eine Spaltenüberschrift sortiert danach, ein zweiter dreht die Reihenfolge um. Ein Klick auf den Titel öffnet das Album: Titel und Titelbild (beides änderbar), Titelliste, verknüpfte Tags, Probleme. „Neue Ordner suchen“ nimmt Alben auf, die direkt auf die Box kopiert wurden. |
| Radio | alle Radiosender: anlegen, umbenennen, Adresse ändern, Titelbild wählen, testen, löschen |
| Spotify | die Spotify-Einträge: aus einem Link anlegen, umbenennen, Link ändern, Titelbild wählen, testen, löschen; auf einer Box auch Konto und Player (Abschnitt 7.6) |
| Bluetooth | der gekoppelte Kopfhörer: Name, verbunden, Akku, Lautstärke, Startwert und Maximum; „Koppeln“ und „Kopplung aufheben“ |
| Statistik | was auf dieser Box gehört wurde: je Tag (ein Klick auf eine Spaltenüberschrift sortiert die Tabelle danach, ein zweiter dreht die Reihenfolge um), je Kalendertag mit den Uhrzeiten, zwischen denen gehört wurde, über Lautsprecher oder Kopfhörer, je Radiosender; übersprungene Titel; je Version der Box-Software Starts, Fehler und Rückfälle, wie viele Tage sie installiert war und wie viele Stunden die Box mit ihr eingeschaltet war (gesamt) und davon etwas gespielt hat (aktiv); ganz unten die Figuren, die nie aufgelegt wurden. Gezählt wird in der Bibliothek: Ist sie nicht erreichbar, steht das im Tab. Eine Box ohne Bibliothek zählt selbst und reicht so weit zurück wie die letzten 1000 Einträge ihres Journals. |
| Hochladen | ein neues Album aus MP3-Dateien anlegen |
| System | Zustand der Box, Sprache der Seite, „Konsistenz prüfen“, „Fortschritte zurücksetzen“, Hardware (Zustand von Tasten, LED und RFID-Leser, für die Fehlersuche), Backup (Abschnitt 9.1), Bibliothek (Abschnitt 11.2), Version (Abschnitt 11.4), eigenes Passwort ändern, Benutzer, Fortschritt je Tag |
| Log | die Meldungen der Box, live, mit Filter |

Die Tasten im Tab „Jetzt“ wirken wie die Tasten der Box.

---

## 7. Alben, Tags und Radiosender verwalten

Alles in diesem Abschnitt braucht die Rolle `admin`.

### 7.1 Ein neues Album auf die Box bringen

1. Tab „Hochladen“.
2. Den Titel des Albums eingeben. Der Ordner auf der Box bekommt denselben Namen.
3. Wer mag, wählt ein Titelbild (JPEG, PNG oder WebP). Es lässt sich auch später setzen.
4. MP3-Dateien in das Feld ziehen, oder „Dateien wählen“ bzw. „Ordner wählen“.
5. Die Reihenfolge prüfen. Sie folgt der Titelnummer in den Dateien, sonst dem Dateinamen (2 vor 10), und lässt
   sich vor dem Anlegen ändern.
6. Optional unter „Mit Tag verknüpfen“ einen Tag wählen.
7. „Album anlegen“.

Grenzen: nur MP3-Dateien, höchstens 500 MB je Datei und 500 Dateien je Album; auf der Box müssen danach 200 MB frei
bleiben. Ein abgebrochener Upload steht unter „Unfertige Uploads“ und verfällt nach einer Stunde.

Titel und Titelbild ändern: Tab „Alben“, Album öffnen. Den Titel oben überschreiben – er ist gespeichert, sobald
das Feld verlassen wird. „Titelbild ändern“ nimmt ein neues Bild (JPEG, PNG oder WebP) und ersetzt das bisherige. Ein
Album ohne Titelbild zeigt das Bild aus seinen MP3-Dateien, falls sie eines enthalten.

Ein Album löschen: Tab „Alben“, Album öffnen, „Album entfernen“ – mit „Dateien ebenfalls löschen“ verschwinden auch
die MP3-Dateien. Das geht nur, wenn kein Tag mit dem Album verknüpft ist und es nicht gerade spielt.

Zeigt ein Album Probleme (LED blinkt rot): Album öffnen. Dort stehen die Probleme, und „Playlist aus den MP3-Dateien
neu erzeugen“ schreibt die Titelliste neu.

### 7.2 Einen Tag mit einem Album verknüpfen

Neuer Tag:

1. Tag auf die Box legen. Die LED leuchtet gelb.
2. Tab „Jetzt“: Unter „Unbekannter Tag aufgelegt“ das Album oder den Sender wählen.
3. „Verknüpfen & abspielen“. Das Album startet sofort.

Bekannter Tag:

- Tab „Tag-Verwaltung“, Spalte „Album / Sender“: ein anderes Album wählen, oder „— nicht verknüpft —“.
- Die Verknüpfung lösen geht auch im Tab „Jetzt“ („Verknüpfung lösen“ für den aufgelegten Tag) und im
  Album-Dialog (✕ am Tag). Der Fortschritt bleibt dabei erhalten.
- Einem Tag im Tab „Tag-Verwaltung“ einen Namen geben, z. B. „rote Katze“ – das erleichtert die Zuordnung.
- Einen Tag löschen: Tab „Tag-Verwaltung“. Sein Fortschritt geht mit verloren.

Mehrere Tags können dasselbe Album haben; jede hat ihren eigenen Fortschritt.

Gibt es eine Bibliothek, lässt sich ein neuer Tag auch ohne Box anlegen: mit dem Telefon (Abschnitt 11.5).

### 7.3 Radiosender

Ein Tag kann statt eines Albums einen Internet-Radiosender spielen. Der Sender spielt, solange der Tag liegt;
einen Fortschritt gibt es nicht.

1. Tab „Radio“, „Neuen Sender anlegen“: Name und Adresse eingeben.
2. „Testen“ spielt die Adresse eine Minute lang, auch bevor sie gespeichert ist. Das geht nur, solange kein Tag
   auf der Box steht; wird eine aufgestellt, endet der Test.
3. „Anlegen“.
4. Tab „Tag-Verwaltung“: dem Tag den Sender zuordnen.

- **Adresse:** der Stream selbst (z. B. `https://…/stream.mp3`) oder eine Playlist, die ihn nennt (`.m3u`, `.pls`,
  `.m3u8`). Sie beginnt mit `http://` oder `https://`.
- **Abbruch:** Ist der Stream nicht erreichbar oder bricht er ab, verbindet die Box nach 1, 2, 5, 10 und dann alle
  30 Sekunden neu. Die LED leuchtet so lange aqua; der Grund steht im Tab „Jetzt“.
- Wird die Adresse eines Senders geändert, der gerade spielt, startet er mit der neuen Adresse.
- **Titelbild:** Im Tab „Radio“ auf das Bild vor dem Sender klicken und ein Bild wählen (JPEG, PNG oder WebP). Es
  ersetzt das bisherige und erscheint auch im Tab „Jetzt“ und bei den Tags des Senders.
- Ein Sender, der mit Tags verknüpft ist oder gerade spielt, lässt sich nicht löschen. Erst die Verknüpfung lösen.

Beispiele:

| Sender | Adresse |
| --- | --- |
| 88.6 | `http://radio886.at/streams/radio_88.6/mp3` |
| Ö1 | `https://orf-live.ors-shoutcast.at/oe1-q2a` |
| Ö3 | `https://orf-live.ors-shoutcast.at/oe3-q2a` |
| FM4 | `https://orf-live.ors-shoutcast.at/fm4-q2a` |

### 7.4 Titelbilder

Ein Tag zeigt das Titelbild seines Albums oder Senders, in den Tabs „Tags“, „Tag-Verwaltung“ und „Jetzt“. Ein Tag hat
kein eigenes Bild: zwei Tags mit demselben Album zeigen dasselbe Titelbild, und ein Tag ohne Verknüpfung zeigt keines.

- Titelbild eines Albums: Tab „Alben“, das Album öffnen, „Titelbild ändern“ (Abschnitt 7.1).
- Titelbild eines Senders: Tab „Radio“, auf das Bild vor dem Sender klicken (Abschnitt 7.3).

### 7.5 Fortschritt

- Tab „System“, „Fortschritt je Tag“: die gespeicherte Stelle jedes Tags.
- „Fortschritte zurücksetzen“ setzt alle Alben auf den Anfang.
- Ein einzelnes Album beginnt von vorn mit „zurück“ 1 s halten an der Box oder „Album von vorne“ im Tab „Jetzt“.

### 7.6 Spotify

Ein Tag kann ein Album, eine Playlist, ein Hörbuch oder einen Podcast von Spotify spielen. Er spielt wie ein Album: Er
setzt fort, wo er aufgehört hat, „weiter“ und „zurück“ wirken. Dafür braucht es Internet, ein Spotify-Premium-Konto
für den Key und ein eigenes Spotify-Konto für die Box.

Einmal je Box, Tab „Spotify“, Karten „Konto“ und „Player“:

1. Der Key: auf developer.spotify.com, Dashboard → „Spotify Soloist API Key“, angemeldet mit einem Premium-Konto. Unter
   „Konto“ einfügen und speichern. Die Box holt den Player von Spotify; „Player“ zeigt dann „Bereit“.
2. „Verbinden“, dann die Box innerhalb von fünf Minuten in der Spotify-App eines Handys im selben WLAN auswählen –
   angemeldet mit dem Spotify-Konto der Box.

Dann für jeden Eintrag:

3. In der Spotify-App: „Teilen“ → „Link kopieren“. Tab „Spotify“, „Neuen Eintrag anlegen“: den Link einfügen. Name
   und Titelbild kommen von Spotify; einen Podcast benennt man selbst.
4. „Testen“ spielt ihn eine Minute lang – nur solange kein Tag aufliegt.
5. Tab „Tag-Verwaltung“: dem Tag den Eintrag zuordnen.

- **Ein eigenes Konto:** Ein Spotify-Konto spielt immer nur an einem Ort. Die Box hat ein eigenes Konto (ein
  Familien-Abo hat sechs), damit ein Tag auf der Box niemandem die Musik anhält.
- **Das Handy hat keinen Einfluss,** solange ein Tag spielt: Was es auf der Box ändert, macht die Box rückgängig. Musik,
  die anderswo mit dem Konto der Box startet, pausiert die Box; „Play“ holt sie zurück.
- **Fortsetzen:** Spotify beginnt immer beim ersten Titel; die Box springt leise zum gespeicherten. Bei einer langen
  Playlist dauert das einige Sekunden; die LED leuchtet so lange aqua.
- **Spotify-Lautsprecher:** Ist „Spotify-Lautsprecher, solange kein Tag aufliegt“ eingeschaltet, kann die Spotify-App
  des Kontos der Box auf der Box spielen, solange kein Tag aufliegt. Die Tasten der Box steuern es, ein Tag gewinnt.
  Die Lautstärke bleibt immer die der Box.
- **Der Player** von Spotify läuft 90 Tage nach seinem Build ab. Die Box sucht ab 30 Tagen davor selbst einen neuen;
  „Jetzt aktualisieren“ fragt sofort bei Spotify.
- Key und Anmeldung bleiben auf der Box: Sie sind nicht in der Bibliothek und nicht im Backup.

---

## 8. Lautstärke begrenzen

Lautsprecher und Kopfhörer haben je einen Startwert und ein Maximum:

| | Startwert | Maximum |
| --- | --- | --- |
| Lautsprecher | gilt beim Einschalten der Box | gilt immer |
| Kopfhörer | gilt bei jedem Verbinden | gilt immer, auch für die Tasten am Kopfhörer |

Ändern (Rolle `admin`): Tab „Jetzt“, „ändern“ unter dem Lautstärkeregler – oder Tab „Bluetooth“, „Startwert und
Maximum ändern“.

- Es gilt: 0 ≤ Startwert ≤ Maximum ≤ 100.
- Ein gesenktes Maximum senkt eine höhere aktuelle Lautstärke sofort.
- Ein neuer Startwert wirkt beim nächsten Einschalten bzw. Verbinden.
- Die Werte bleiben erhalten, auch nach dem Ausschalten.
- Die Rolle `user` sieht die Werte, kann sie aber nicht ändern. So verstellt niemand den Gehörschutz nebenbei.
- Die Tasten am Kopfhörer kann die Box nur bei Kopfhörern begrenzen, die ihre Lautstärke mit der Box abgleichen
  („Absolute Volume“). Die Lautstärke, die die Box selbst einstellt, ist immer begrenzt.

---

## 9. Benutzer und Passwörter

- **Eigenes Passwort ändern** (jeder Benutzer): Tab „System“, „Eigenes Passwort ändern“ – mit dem aktuellen
  Passwort. Auf allen anderen Geräten ist danach eine neue Anmeldung nötig.
- **Benutzer verwalten** (Rolle `admin`): Tab „System“, „Benutzer“ – Benutzer anlegen (Rolle `user` oder `admin`),
  Passwort setzen, löschen.
  - Ein neues Passwort meldet den Benutzer überall ab.
  - Den eigenen Benutzer kann man nicht löschen. So bleibt immer ein Admin.
  - Die Rolle eines bestehenden Benutzers lässt sich auf der Webseite nicht ändern: Benutzer löschen und neu
    anlegen.
- Passwörter haben mindestens 8 Zeichen.
- **Von selbst abgemeldet:** Eine Seite, auf der 30 Minuten lang nichts bedient wurde, meldet sich ab und sagt das
  auf der Anmeldeseite. Als Bedienung zählt eine Mausbewegung, ein Tippen oder eine Taste; ein laufender Upload zählt
  auch. Die Zeit lässt sich in der Konfiguration der Box oder der Bibliothek ändern oder abschalten
  ([configuration.md](../../developer/configuration.md), Abschnitt 5).
- Passwort vergessen: Ein Admin setzt ein neues. Kennt kein Admin sein Passwort mehr, hilft nur der Zugang zur Box
  selbst ([operations.md](../../developer/operations.md), Abschnitt 4).

### 9.1 Backup

Ein Backup ist eine Datei mit den Daten der Box: Tags, Verknüpfungen, Fortschritte, Lautstärken und die Titelbilder
der Alben und Sender.
Nur die Rolle `admin` kann eines erstellen.

- Tab „System“, „Backup“: **„Backup herunterladen“** speichert die Datei auf dem Gerät, an dem man sitzt. Sie ist
  klein.
- **„Backup mit MP3s herunterladen“** nimmt auch die Alben mit. Die Datei ist dann so groß wie alle Alben zusammen,
  und das Herunterladen dauert entsprechend.
- Benutzer, Passwörter und Schlüssel sind nie in einem Backup.
- Eine Box, die mit einer Bibliothek arbeitet, braucht kein eigenes Backup: Alles steht in der Bibliothek, und die
  sichert sich selbst (Abschnitt 11.6).

### 9.2 Regelmäßig sichern

Eine Box ohne Bibliothek – und die Bibliothek (Abschnitt 11.6) – erstellt von selbst Backups: Tab „System“, Karte
„Backup“, „Regelmäßig sichern“. Es gibt zwei Zeitpläne, unabhängig voneinander: einen für die Backups ohne MP3s,
einen für die mit MP3s.

| Einstellung | Bedeutung |
| --- | --- |
| regelmäßig | Backups dieser Art entstehen von selbst. |
| alle … um … | wie oft – eine Zahl von Tagen, Wochen oder Monaten – und zu welcher Uhrzeit. Bei Monaten: am selben Tag des Monats wie das letzte Backup. |
| … Backups aufbewahren | Ältere Backups dieser Art werden gelöscht. |
| Ordner | wo diese Backups abgelegt werden. Leer: der vorgegebene Ordner, auf einer Box `backups` neben den Alben. |

- Ein Beispiel: ohne MP3s jede Woche, 52 aufbewahren – ein Jahr kleiner Backups; mit MP3s jeden Monat, 2
  aufbewahren – das aktuelle und das davor, jedes so groß wie alle Alben.
- „Speichern“ übernimmt die Einstellungen beider Zeitpläne. „Jetzt sichern“ und „Jetzt mit MP3s sichern“ erstellen
  sofort ein Backup; es landet im Ordner seiner Art und zählt für deren Anzahl.
- Die Tabelle darunter zeigt die Backups beider Ordner mit ihrer Art. Jedes lässt sich herunterladen und löschen.
- Unter jedem Zeitplan steht, wo seine Backups liegen, wann das letzte erstellt wurde und wann das nächste fällig
  ist – oder warum das letzte fehlgeschlagen ist.
- Ändert man einen Ordner, bleiben die bisherigen Backups im alten Ordner liegen.
- Eine Box, die mit einer Bibliothek arbeitet, zeigt diesen Teil nicht: Die Bibliothek erstellt die Backups. Die
  Einstellungen bleiben erhalten und gelten wieder, wenn die Box von der Bibliothek getrennt wird.
- Auf einer Box liegen die Backups auf ihrer SD-Karte, wie die Daten selbst. Gegen eine kaputte Karte hilft nur ein
  Backup, das heruntergeladen wurde, oder ein Ordner auf einem anderen Laufwerk.

### 9.3 Backup zurückspielen

Ein Backup ersetzt die Daten der Box – oder der Bibliothek – durch die vom Tag, an dem es erstellt wurde. Nur die
Rolle `admin` kann das: Tab „System“, Karte „Backup“, „Backup zurückspielen“.

1. **„Backup wählen …“** und die Datei auf dem Gerät wählen, an dem man sitzt. Bei einem Backup, das ein Zeitplan
   erstellt hat: „Zurückspielen“ daneben in der Tabelle.
2. Das Backup wird gesendet und bis zum Ende gelesen; mit MP3s dauert das so lange wie ein Download. Eine Datei, die
   kein Backup ist, abgeschnitten ist oder zur Bibliothek statt zu einer Box gehört, wird abgelehnt – dann ändert
   sich nichts.
3. Die Seite fragt einmal: „Das ersetzt alle Daten durch die vom …“. Mit MP3s werden alle Alben gelöscht und
   ersetzt. Abbrechen, und nichts ändert sich.
4. Nach „OK“ hält die Box (oder die Bibliothek) an, legt die Daten an ihren Platz und startet neu. Die Seite lädt
   neu; danach erneut anmelden.

- Vorher wird kein Backup erstellt. Wenn die Daten von jetzt noch gebraucht werden könnten: abbrechen und zuerst
  ein Backup herunterladen.
- Benutzer, Passwörter, Schlüssel und wohin die Backups gehen bleiben, wie sie sind.
- Ein Backup ohne MP3s lässt die Alben, wie sie sind. Nennen die Daten ein Album, dessen Ordner fehlt, sagt die Karte
  das danach und nennt es.
- Eine Box, die mit einer Bibliothek arbeitet, bekommt bei der nächsten Synchronisierung wieder den Stand der
  Bibliothek. Dann besser das Backup auf der Bibliothek zurückspielen (Abschnitt 11.6).

---

## 10. Wenn etwas nicht funktioniert

| Beobachtung | Ursache und Abhilfe |
| --- | --- |
| LED aus | kein Strom: Powerbank und Kabel prüfen |
| LED bleibt rot | Die Box startet nicht. Strom trennen und wieder anschließen. Bleibt es dabei: Administrator. |
| LED grün, Tag wird nicht erkannt | Tag mittig auflegen. Anderen Tag probieren. Erkennt die Box keinen Tag mehr: Strom trennen und wieder anschließen. |
| LED gelb | Der Tag ist nicht verknüpft: Abschnitt 7.2 |
| LED gelb/rot im Wechsel | Das Album des Tags wurde gelöscht: neu verknüpfen (Abschnitt 7.2) |
| LED rot blinkend | Das Album ist defekt: Tab „Alben“, Album öffnen (Abschnitt 7.1) |
| LED aqua, kein Ton (Radio) | Der Sender ist nicht erreichbar: WLAN prüfen; der Grund steht im Tab „Jetzt“ |
| LED grün → aqua → blau, es dauert | Die Box holt das Album aus der Bibliothek. Der erste Titel braucht je nach WLAN einige Sekunden. Wird die LED rot blinkend: Die Bibliothek ist nicht erreichbar, und das Album war noch nie auf dieser Box. |
| LED rot, weiß, magenta, gelb im Wechsel | Abschnitt 11.4 |
| LED weiß/blau, kein Ton | Pause. Play, „weiter“ oder Tag abnehmen und wieder auflegen (Abschnitt 4) |
| LED blau, kein Ton | Ist ein Kopfhörer verbunden? Dann spielt nur er. Lautstärke prüfen. |
| Kopfhörer verbindet sich nicht | Kopfhörer aus- und einschalten, 15 s warten. Mit einem Handy verbunden? Dort trennen. Sonst neu koppeln (Abschnitt 5.1). |
| Koppeln: LED blinkt rot | Der Kopfhörer war nicht im Pairing-Modus oder zu weit weg: wiederholen. |
| Ton im Kopfhörer wird von anderen Tönen überlagert | Der Kopfhörer ist auch mit einem Handy verbunden (Abschnitt 5.6). |
| Webseite öffnet nicht | Gerät im selben WLAN? Box eingeschaltet (LED)? `https://` vorangestellt? |
| Anmeldung abgelehnt | Passwort prüfen. Nach mehreren Fehlversuchen 30 Sekunden warten. |
| Album startet von vorn statt an der alten Stelle | Das Album war zu Ende gehört, oder der Tag wurde mit einem anderen Album verknüpft, oder die Titelliste wurde gekürzt. |

Fehlersuche für Administratoren: [operations.md](../../developer/operations.md), Abschnitt 9.

---

## 11. Bibliothek

Die Bibliothek ist ein Programm im Heimnetz, zum Beispiel auf dem NAS. Sie hält alle Alben, Tags, Radiosender und
Titelbilder. Jede Box holt sich von dort, was ihr fehlt – und spielt auch ohne die Bibliothek, was sie schon hat. Eine
Box ohne Bibliothek funktioniert wie bisher.

### 11.1 Was sich für die Box ändert

- **Alle Boxen kennen dieselben Tags.** Ein Tag, der an einer Box oder in der Bibliothek verknüpft wurde,
  spielt auf jeder Box.
- **Alben kommen bei Bedarf.** Liegt ein Tag auf einer Box, die das Album noch nicht hat, holt die Box es. Die LED
  wechselt dabei grün → aqua → blau, und die Box spielt, sobald der erste Titel da ist.
- **Der Fortschritt gehört zur Box.** Derselbe Tag spielt im Kinderzimmer an einer anderen Stelle weiter als in
  der Küche.
- **Ohne WLAN geht nichts verloren.** Was an einer Box geändert wird, gilt dort sofort und erreicht die Bibliothek,
  sobald die Box sie wieder erreicht. Ändern beide denselben Tag, gilt der Stand der Bibliothek.
- **Der Platz auf der SD-Karte regelt sich selbst.** Wird er knapp, löscht die Box die Dateien des Albums, das am
  längsten nicht gehört wurde. Die Bibliothek hat es weiter, und die Box holt es wieder, wenn der Tag aufgelegt
  wird.

### 11.2 Eine Box mit der Bibliothek verbinden

Rolle `admin`, Tab „System“, Abschnitt „Bibliothek“:

1. Die Adresse der Bibliothek eintragen, z. B. `https://nas:8866`.
2. Beim ersten Mal das Bootstrap-Passwort eintragen. Es wurde bei der Installation der Bibliothek festgelegt.
3. „Speichern“, dann die Box aus- und einschalten.

Danach zeigt der Abschnitt „verbunden“, und die Box erscheint auf der Seite der Bibliothek. Was die Box vorher schon
hatte, wandert in die Bibliothek; Alben, die es dort schon gibt, werden erkannt und nicht doppelt angelegt.
„Noch nicht bestätigt“ zählt die Änderungen, die die Bibliothek noch nicht übernommen hat. Bei „Diese Box“ steht
der Name der Box; mit „Umbenennen“ daneben bekommt sie einen anderen, z. B. „Kinderzimmer“. Unter ihm zeigt die
Bibliothek sie.

**Verbindung lösen:** „Verbindung zur Bibliothek lösen“ im selben Abschnitt. Die Box behält alles, was auf ihrer
SD-Karte liegt – Tags, Alben, Sender, Fortschritte – und arbeitet danach für sich allein. Die Bibliothek wird dabei
nicht gefragt; sie darf ausgeschaltet sein oder gar nicht mehr existieren. Vorher sagt die Seite, was verloren
geht: Änderungen, die die Bibliothek noch nicht übernommen hat, kommen dort nicht mehr an, und Alben, die nicht
vollständig auf der SD-Karte liegen, lassen sich nicht mehr holen. Mit „Abbrechen“ wartest du, bis die Box
abgeliefert und geholt hat. Die Bibliothek zeigt die Box weiter, bis sie dort entfernt wird (Tab „Boxen“). Wieder
verbinden geht wie beim ersten Mal, mit Adresse und Bootstrap-Passwort.

### 11.3 Die Seite der Bibliothek

Die Seite der Bibliothek sieht aus wie die einer Box, ohne „Jetzt“ und „Bluetooth“. Sie hat eigene Benutzer: Wer
sich an einer Box anmelden kann, kann es dort nicht von selbst; ein Admin der Bibliothek legt die Benutzer in ihrem
Tab „System“ an. Alben, Tags, Sender und Titelbilder verwaltet man dort wie in Abschnitt 7.

Im Tab „Alben“ ist ein Album mit „defekt“ markiert, wenn eine seiner MP3-Dateien kaputt ist: weil die Bibliothek
die Datei nicht lesen kann oder weil eine Box beim Abspielen Fehler in ihr fand. Der Albumdialog nennt die Dateien.
Ersetzt man die Datei, verschwindet die Markierung; eine Meldung einer Box, die nichts über die Datei sagt,
entfernt ein Administrator dort mit „Meldungen verwerfen“.

Diese Tabs hat nur die Bibliothek; den Tab „Statistik“ hat sie für alle Boxen:

| Tab | Inhalt |
| --- | --- |
| Boxen | alle Boxen mit Version und letztem Kontakt. Umbenennen, entfernen (z. B. nach einer verlorenen SD-Karte), die Fortschritte einer alten Box auf eine neu eingerichtete übernehmen. Alben, die gerade von einer Box hochgeladen werden. Für Administratoren: Bootstrap-Passwort ändern, ein Token für Skripte erzeugen, die Dateien der Alben und Karten prüfen. |
| Versionen | die Versionen der Box-Software. Alte Versionen, die nicht mehr im Archiv sind und auf keiner Box laufen, fehlen, solange „alte Versionen ausblenden, die nicht im Archiv sind“ angehakt ist; die Seite sagt, wie viele. `use`: Boxen installieren sie von selbst. `manual`: nur von Hand. `dontuse`: jede Box verlässt sie sofort. „Läuft auf“ nennt die Boxen, auf denen eine Version läuft, und die Bibliothek selbst, „Vorbereitet auf“ die Boxen, die sie schon geholt haben und sie ausprobieren, sobald sie das nächste Mal eingeschaltet werden. Unter der Tabelle wird eingestellt, wie viele Versionen die Bibliothek höchstens aufbewahrt (anfangs 5); was eine Box noch braucht, bleibt immer. Unter „Zugang zu den Releases“ steht, woher neue Versionen kommen; dort wird das Token gespeichert, mit dem die Bibliothek sie abholen darf. Bei GitHub genügt ein Fine-grained Token für dieses eine Repository mit dem Recht „Contents: Read-only“. |
| Statistik | dasselbe wie der Tab „Statistik“ einer Box (Abschnitt 6.3). Gibt es mehrere Boxen, wählt man oben „Alle Boxen“ oder eine davon. „Alle Boxen“ zählt je Tag, Radiosender, übersprungenem Titel und Version zusammen und nennt bei jedem Tag, auf welcher Box er wie lange lief; eine Tabelle der Boxen zeigt Hördauer und letztes Auflegen, ein Klick auf eine Box wählt sie. Die Tage werden nie zusammengezählt: Jede Box hat ihre eigene Zeile mit ihren Uhrzeiten. |

### 11.4 Version der Box

Eine Box, die dafür eingerichtet ist, holt neue Versionen selbst aus der Bibliothek und probiert sie beim nächsten
Einschalten aus. Funktioniert die neue Version nicht, läuft nach wenigen Sekunden wieder die vorige. Tab „System“,
Abschnitt „Version“ (Rolle `admin`):

| Taste | Wirkung |
| --- | --- |
| Nach neuer Version suchen | zeigt, was installiert ist und was die Bibliothek anbietet |
| Jetzt aktualisieren | holt die neueste Version und startet die Box neu |
| Updates: aus, nur von Hand, automatisch | ob die Box neue Versionen von selbst installiert. „Aus“: Sie sieht nicht einmal nach. Bei einer Box ohne Bibliothek heißt „nur von Hand“ „nachfragen“. |
| Zurück zur vorigen Version | sofort, auch wenn gerade etwas spielt. Die verlassene Version installiert danach keine Box mehr von selbst. |
| Notfall-Version starten | die Version, mit der die Box eingerichtet wurde, oder die zuletzt gemerkte |
| Als Notfall-Version merken | die laufende, bewährte Version wird die Notfall-Version |

Wechselt die LED zwischen rot, weiß, magenta und gelb, ist die Bibliothek zu alt für die Version der Box. Die Box
spielt dann nichts. Abhilfe: die Bibliothek aktualisieren, an der Box „Zurück zur vorigen Version“, oder die
Verbindung zur Bibliothek lösen (Abschnitt 11.2).

**Eine Box ohne Bibliothek** holt neue Versionen selbst: Einmal am Tag sieht sie bei GitHub nach, ob es ein neues
Release gibt. „Quelle“ im Abschnitt „Version“ sagt, woher die Versionen kommen. Solange eine Box mit einer
Bibliothek verbunden ist, kommt nichts von GitHub.

- Ein neues Release installiert die Box nicht sofort, sondern nach einer Wartezeit von drei Tagen: Folgt in der
  Zeit eine Korrektur, lädt sie nur diese. Der Abschnitt zeigt unter „Neu“ die Version und den Tag, an dem sie von
  selbst installiert wird. „Jetzt installieren“ wartet nicht.
- Installiert wird im Hintergrund; die neue Version startet beim nächsten Einschalten der Box.
- Mit „Updates: nachfragen“ lädt die Box nichts von selbst. Sie zeigt Administratoren oben auf jeder Seite
  „Version … ist verfügbar“, bis jemand antwortet: „Installieren“, oder „Überspringen“ – dann fragt sie erst beim
  nächsten Release wieder. „Was ist neu“ zeigt, was sich geändert hat. Mit „aus“ fragt die Box GitHub gar nicht.
- Bei der Anfrage erfährt GitHub die Adresse des Haushalts, sonst nichts: keinen Namen, keine Version, nichts
  davon, was gehört wurde.

### 11.5 Neue Tags mit dem Telefon scannen

Ein Android-Telefon mit NFC kann einen Tag lesen. So kommt ein neuer Tag in die Bibliothek, ohne dass man sie auf
eine Box stellt.

1. Auf dem Telefon in **Chrome** die Seite der Bibliothek öffnen und anmelden.
2. Tab „Tag-Verwaltung“, unten bei „Neuen Tag anlegen“: „Tag scannen“. Beim ersten Mal fragt Chrome, ob die Seite NFC
   verwenden darf.
3. Den Tag an die Rückseite des Telefons halten. Die Seite zeigt den Tag mit seiner UID; eine neue ist mit „neu“
   markiert und der Bibliothek schon hinzugefügt.
4. Als `admin` gleich dort den Namen eintragen, das Album oder den Sender wählen. Das
   geht auch später in der Tabelle darüber, auf jedem Gerät.
5. Weitere Tags einfach nacheinander an das Telefon halten. „Scannen beenden“ hört auf.

Kann das Telefon oder sein Browser keine Tags lesen, sagt die Seite beim Tippen auf „Tag scannen“, woran es
liegt. Auf einem iPhone geht es nicht.

Meldet die Seite, dass Chrome ihr kein NFC erlaubt, wurde die Frage von Chrome mit „Blockieren“ beantwortet oder
NFC ist in Chrome für Websites ausgeschaltet. Erlauben: links neben der Adresse auf das Symbol tippen, dann
„Berechtigungen“ und „NFC-Geräte“. Fehlt der Eintrag dort: Chrome-Menü, „Einstellungen“, „Website-Einstellungen“,
„NFC-Geräte“ – der Schalter muss an sein, und die Seite der Bibliothek darf nicht unter „Blockiert“ stehen.

- Die Boxen kennen den neuen Tag kurz darauf.
- Auf anderen geöffneten Seiten ändert sich nichts von selbst; der neue Tag steht im Tab „Tag-Verwaltung“, sobald man ihn
  öffnet.
- Scannen darf jeder angemeldete Benutzer, verknüpfen nur die Rolle `admin`.
- Der Tag wird nur gelesen. Auf einen Tag wird nie etwas geschrieben.
- Lesen kann nur Chrome auf einem Android-Telefon. Auf dem PC fehlt der Knopf. Das Telefon muss entsperrt und die
  Seite im Vordergrund sein.

### 11.6 Backups der Bibliothek

Die Bibliothek hat im Tab „System“ dieselbe Karte „Backup“ wie eine Box (Abschnitt 9.1). Ihr Backup enthält den
Katalog, die Fortschritte und Einstellungen aller Boxen, die Statistik und die Titelbilder der Alben und Sender, auf
Wunsch auch die Alben.

Zusätzlich sichert sich die Bibliothek selbst, unter „Regelmäßig sichern“ (Abschnitt 9.2):

- Die Ordner sind Ordner auf dem NAS, z. B. `/volume1/tinyjukebox/backups`. Leer: der vorgegebene Ordner `backups`
  in der Freigabe `tinyjukebox`. Die Bibliothek muss in den Ordner schreiben dürfen.
- Jedes Backup mit MP3s belegt noch einmal so viel Platz wie alle Alben.
- Ein Backup wird wie auf einer Box zurückgespielt (Abschnitt 9.3). Die Boxen übernehmen den Katalog des Backups bei
  ihrer nächsten Synchronisierung.
