# Rushbase - Anleitung für Redaktion und Produktion

[English version](en.md)

Rushbase ist die Mediathek eures Teams: Rohmaterial (Video, Foto, Audio) liegt auf dem
Netzwerkspeicher, Rushbase erkennt es, lässt es von einem Rechner analysieren und macht es
durchsuchbar. Diese Anleitung ist für alle, die Material **finden, prüfen und weiterverwenden** -
nicht für die, die Rushbase installieren (das steht in [operations.md](../operations.md)).

![Übersicht](../screenshots/dashboard-light.png)

## Das Wichtigste in Kürze

- Kopiere neues Material in den Ordner `Input/` auf dem Speicher. Mehr musst du nicht tun.
- Rushbase **verändert deine Originale nie**. Es liest, analysiert und sortiert sie nur in die
  Bibliothek (`Content/`) um. Einmal einsortiert, bleibt eine Datei für immer an ihrem Platz, damit
  Schnittprojekte nicht brechen.
- **Suchen geht immer**, auch wenn der Analyse-PC aus ist. Für _neue_ Beschreibungen, Transkripte
  und Szenen muss der Analyse-PC (Worker) laufen.
- **Deine Korrekturen gewinnen immer** gegen die KI.

## 1. Material hineingeben

1. Lege auf dem Speicher unter `Input/` einen Ordner pro Dreh an, benannt als
   **`JJJJ-MM-TT_Bezeichnung`**, zum Beispiel `2026-10-12_Imagefilm_Standort-A`.
2. Kopiere die Dateien (oder ganze Kartenstrukturen der Kamera) hinein.

Was der Ordnername bedeutet:

| Teil                   | Wirkung                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| `2026-10-12`           | Drehdatum als Hinweis, falls die Kamera kein Aufnahmedatum liefert.                            |
| `Imagefilm_Standort-A` | Bezeichnung des **Stapels**. Sie ist durchsuchbar und landet im Ordnernamen in der Bibliothek. |

Dateien, die direkt in `Input/` liegen, fasst Rushbase pro Tag zu einem automatischen Stapel
("Auto 2026-10-12") zusammen. Besser ist ein eigener Ordner pro Dreh.

Eine Kamerakarte (zum Beispiel `PRIVATE/M4ROOT/...`) kopierst du **als Ganzes** hinein; Rushbase
behandelt sie als Einheit und sortiert sie erst um, wenn alles darin analysiert ist.

### Was danach passiert

Auf der **Übersicht** und unter **Verarbeitung** siehst du den Weg: _Eingang → Analyse → Bibliothek_.

| Status      | Bedeutung                                                                                       |
| ----------- | ----------------------------------------------------------------------------------------------- |
| Kopiert     | Die Dateien werden noch auf den Speicher kopiert. Rushbase wartet, bis sich nichts mehr ändert. |
| Wartet      | Erkannt, wartet auf einen Worker.                                                               |
| In Arbeit   | Wird gerade analysiert.                                                                         |
| Prüfen      | Die Analyse war unsicher, ein Mensch soll draufschauen (siehe Abschnitt 4).                     |
| Einsortiert | Fertig und in `Content/` abgelegt.                                                              |
| Quarantäne  | Die Datei ließ sich nicht verarbeiten (siehe FAQ).                                              |

![Verarbeitung](../screenshots/processing-light.png)

**Der Analyse-PC muss laufen.** Die schwere Arbeit (Szenen, Bildbeschreibung, gesprochener Text)
erledigt ein Worker, typischerweise ein PC mit Grafikkarte, den man nur bei Bedarf einschaltet. Ist
er aus, wartet das Material in der Schlange (die Übersicht zeigt "Kein Worker online"). Sobald er
läuft, arbeitet er alles auf einmal ab. Alles, was schon analysiert ist, bleibt durchsuchbar.

## 2. Suchen und Filtern

Unter **Bibliothek** gibst du einfach ein, was du suchst - in normalen Worten, deutsch oder
englisch: _"Drohne über dem Hafen bei Sonnenuntergang"_, _"Interview Frau mit Brille"_, _"A001"_.
Rushbase kombiniert Volltext (Titel, Dateiname, Kamera, Transkript, Beschreibung) mit einer
Bild- und Bedeutungssuche.

![Bibliothek](../screenshots/library-light.png)

- **Material oder Szenen:** Unter "Trefferart" suchst du ganze Clips (**Material**) oder einzelne
  Einstellungen mit Zeitmarke (**Szenen**). Szenen sind ideal, wenn du genau die zehn Sekunden
  brauchst und nicht den ganzen Clip.
- **Filter:** Links grenzt du nach Medientyp, Kamera, Objektiv, Ort, Tageszeit, Einstellungsgröße,
  Datum, Dauer, Tags und mehr ein. Die Zahlen zeigen, wie viele Treffer jeder Wert hätte.
  "Zurücksetzen" räumt alles ab.
- **Sortieren und Ansicht:** Relevanz, neueste, älteste, längste ...; Raster oder Liste.
- **Ähnliches finden:** Im Material-Detail (Reiter _Ähnliches_) und bei einzelnen Szenen findest du
  Aufnahmen, die so aussehen wie diese - praktisch für Alternativen zu einer Einstellung.
- Erscheint der Hinweis, die semantische Suche sei nicht aktiv, zeigt Rushbase nur Volltext-Treffer.
  Das ist kein Fehler; die Suche wird besser, sobald die Modelle bereit sind.

Mit **Strg+K** (Mac: Cmd+K) öffnest du die Befehlspalette, `?` zeigt alle Tastenkürzel.

## 3. Ein Material ansehen

Ein Klick öffnet die Detailseite.

![Material-Detail](../screenshots/asset-light.png)

- **Player:** Du siehst eine Vorschau (Proxy). Das Original wird nie über den Server gestreamt.
- **Szenen:** Unter dem Player stehen die erkannten Einstellungen mit Schlüsselbildern. Ein Klick
  springt dorthin.
- **Reiter:** _Technik_ (Codec, Auflösung, Kamera ...), _Dateien_, _Verlauf_, _Transkript_
  (durchsuchbar, Klick auf einen Abschnitt springt zur Stelle), _Tags_, _Beschreibung_ (KI-Text
  auf Deutsch und Englisch), _Datenschutz_ (siehe Abschnitt 9), _Ton-Sync_ (nur wenn ein Vorschlag
  existiert) und _Ähnliches_.
- **Tags und ihre Herkunft:** Jeder Tag zeigt, woher er kommt: **KI** (automatisch erkannt),
  **Claude** (von Claude gesetzt) oder **Von Hand** (von einer Person). Von Hand schlägt Claude,
  Claude schlägt KI.
- **Pfad kopieren:** Mit "Pfad kopieren" bekommst du den Dateipfad für **dein Betriebssystem**
  (Windows-Laufwerksbuchstabe, macOS- oder Linux-Mount). Den fügst du in deinen Schnittplatz ein.
  "Relativen Pfad kopieren" gibt den Pfad ab der Bibliothekswurzel.

## 4. Prüfen und KI-Ergebnisse korrigieren

Die KI irrt sich manchmal. Unter **Prüfen** liegt alles, bei dem sie selbst unsicher war - mit
Grund ("unsicher bei Tageszeit", "Bildanalyse und Bildsuche widersprechen sich", "keine Tags").

- **Freigeben** (Taste `A`): Du hast draufgeschaut, es ist in Ordnung.
- **Korrigieren:** Öffnet das Material. Ändere Titel, Bewertung, Beschreibung, Einstellungsgröße,
  Tageszeit ... oder füge Tags hinzu und entferne falsche.
- **Sperren** (Taste `L`): Gesperrtes Material ändert die Analyse **nie** wieder automatisch.
  Nutze das für alles, was du fertig kuratiert hast.
- Mit `J`/`K` oder den Pfeiltasten wechselst du zum nächsten Eintrag.

Wichtig zu wissen:

- Was du **von Hand** setzt, wird nie von einer erneuten Analyse überschrieben.
- Einen Tag, den du entfernst, setzt die KI nicht wieder.
- Auch bei nicht gesperrtem Material bleiben deine Änderungen bestehen; die Sperre schützt
  zusätzlich vor _allen_ automatischen Änderungen.

### Taxonomie: neue Begriffe freigeben

Tags kommen aus einem festen Vokabular (Feld → Werte, zum Beispiel _Ort → Hafen_). Entdeckt die KI
einen neuen Begriff, **schlägt** sie ihn nur vor. Unter **Taxonomie → Vorschläge** siehst du, wie
oft er vorkam und an welchen Beispielen. Du kannst ihn

- **freigeben** (wird ab dann für Suche und Filter genutzt),
- **zusammenführen** mit einem vorhandenen Wert (zum Beispiel _Hafenbecken_ → _Hafen_) oder
- ignorieren.

Freigeben dürfen Redakteure; Felder anlegen, umbenennen und zusammenführen darf nur ein
Administrator. Änderungen am Vokabular starten die Zuordnung für betroffenes Material neu.

## 5. Bedarfslisten: was uns noch fehlt

Eine **Bedarfsliste** ist eine Einkaufsliste für Bilder: "Für den Imagefilm brauchen wir noch eine
Totale vom Hafen bei Nacht, zwei Nahaufnahmen von Händen ...". Jeder Punkt hat eine Beschreibung,
optionale Filter und eine gewünschte Anzahl.

- Unter **Bedarf** legst du eine Liste an und schreibst die gesuchten Einstellungen, eine pro Zeile.
- Wird neues Material einsortiert, hängt Rushbase **passende Treffer automatisch als Kandidaten** an.
  Niemand wird benachrichtigt; du siehst sie, wenn du die Liste öffnest.
- Pro Kandidat: **Bestätigen** oder **Ablehnen**. Ist die gewünschte Anzahl bestätigt, gilt der
  Punkt als erfüllt ("2 von 3 bestätigt"). Abgelehntes Material wird für den
  Punkt nicht erneut vorgeschlagen.
- Ist das Projekt durch, **schließt** du die Liste (wieder öffnen geht jederzeit).

Du kannst Bedarfslisten auch von Claude anlegen lassen (Abschnitt 8).

## 6. Sammlungen und Mehrfachbearbeitung

In der Bibliothek wählst du mehrere Treffer aus (Haken auf der Kachel, "Alle geladenen" wählt alle
sichtbaren). Dann erscheint die Aktionsleiste:

- **Zur Sammlung** - hängt die Auswahl an eine Sammlung, zum Beispiel pro Projekt. Sammlungen
  sind auch ein Filter in der Bibliothek.
- **Tags hinzufügen oder entfernen** für alle ausgewählten Elemente auf einmal.
- **Sperren** für die ganze Auswahl.

Sammlungen verschieben nichts auf dem Speicher; sie sind nur eine Sicht auf dasselbe Material.

## 7. Die Companion-App

Die Desktop-App "Rushbase Companion" läuft auf deinem Rechner und hat zwei Hauptbereiche.

**Processor: ein Klick zum Verarbeiten.** Wenn dein PC die schwere Analyse übernehmen soll
(zum Beispiel der mit der Grafikkarte), öffne die App. Sie zeigt, wie viele Dateien und Stunden
Material warten und wie lange es etwa dauert. Ein Klick auf **Verarbeiten** startet die Analyse;
du siehst Fortschritt, Restzeit und Protokoll. **Pause/Stopp** gibt angefangene Arbeit sauber an
die Schlange zurück. Optional: nur bis zu einer Uhrzeit arbeiten, nur wenn der PC im Leerlauf ist
oder bei zu hoher Grafikkartentemperatur pausieren.

![Processor](../screenshots/desktop-processor-idle-light.png)

**Bibliothek:** Dieselbe Suche und dieselben Filter wie im Browser, aber mit Vorschaubildern direkt
vom Speicher und - wenn dein System das Format abspielen kann - mit dem Original im Player. Du wählst
Clips oder einzelne Szenen in die **Auswahl** und kannst sie dann

- an **DaVinci Resolve senden**,
- als **Datei exportieren**, oder
- den Pfad kopieren bzw. im Dateimanager zeigen.

![Bibliothek in der App](../screenshots/desktop-library-light.png)

### Resolve Studio oder kostenlose Version?

|                     | Resolve Studio (21.1+)                                                                                                                                                          | Resolve kostenlos                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| "An Resolve senden" | **Ja:** legt Ordner (Bins) an, importiert die Clips ins geöffnete Projekt, setzt Beschreibung, Stichwörter und Marker an den Szenen. Mehrfaches Senden erzeugt keine Dubletten. | Nein (die freie Version lässt sich nicht skripten).                                                 |
| Export              | Auch möglich.                                                                                                                                                                   | **FCPXML** (Szenen als Clips auf einer Timeline), **EDL**, **Metadaten-CSV** zum Import in Resolve. |

Voraussetzung für Studio: In Resolve unter _Einstellungen → System → Allgemein_ "Externe
Skripterstellung" auf "Lokal". Speichere dein Projekt vor dem ersten Senden.

**Tipp für gemischte Teams:** Resolve speichert absolute Pfade. Nutzt auf allen Rechnern denselben
Einhängepfad (oder Resolves "Mapped Mount"), damit Projekte zwischen Windows, macOS und Linux
funktionieren.

## 8. Rushbase mit Claude nutzen

Wenn dein Administrator den **Claude-Connector** eingerichtet hat, kannst du in claude.ai in
normaler Sprache mit eurer Bibliothek arbeiten. Einmalig: in claude.ai unter _Einstellungen →
Konnektoren → Benutzerdefinierten Konnektor hinzufügen_ die Rushbase-Adresse eintragen
(`https://<euer-host>/mcp`), dann auf der Rushbase-Seite mit deinem normalen Konto anmelden und
**Erlauben** klicken.

Claude kann suchen, Details und Schlüsselbilder ansehen, Lücken im Bestand finden, Bedarfslisten
führen und Tags vorschlagen. Claude kann **nie löschen oder verschieben**, und es meldet sich nicht
von selbst - es antwortet nur, wenn du fragst. Jeder Aufruf steht im Protokoll der Verwaltung.

Beispiele:

- _"Welche Drohnenaufnahmen von Standort A haben wir aus diesem Jahr?"_
- _"Erstelle eine Bedarfsliste für ein 60-Sekunden-Imagevideo und prüfe, was fehlt."_
- _"Zeig mir ruhige Nahaufnahmen von Händen bei der Arbeit, nicht älter als sechs Monate."_
- _"Gibt es zu 'Hafen bei Nacht' Alternativen zu diesem Clip?"_
- _"Markiere alle Interviews vom Dreh am 12. Oktober als 'Zu prüfen'."_

Von Claude gesetzte Tags tragen die Herkunft **Claude**. Deine eigenen Korrekturen haben Vorrang.

## 9. Datenschutz: erkennbare Personen und Einwilligung

Für jedes Material (und jede Szene) hält Rushbase fest, ob eine **Person erkennbar** ist (Ja / Nein /
Unbekannt) und, ob eine **Einwilligung** vorliegt (Ja / Nein / Unbekannt, optional mit Verweis auf
das Dokument).

- Die KI schätzt "Person erkennbar" als Hinweis; **entscheiden tut ein Mensch**. Du siehst den Wert
  im Reiter _Beschreibung_ und kannst ihn korrigieren.
- Die **Einwilligung** trägst du ein (oder lässt Claude eintragen: _"Setze die Einwilligung für alle
  Clips vom Interview am 12. Oktober auf Ja, Verweis 'Vertrag 2026-14'"_).
- In der Bibliothek kannst du danach filtern, zum Beispiel "Person erkennbar = Ja und Einwilligung =
  Unbekannt", um zu sehen, was noch geklärt werden muss, bevor es veröffentlicht wird.
- **Material einer Person wiederfinden:** Suche nach ihrem Namen, falls er im Transkript oder in
  einem Tag steht, oder nach der Beschreibung ("Frau mit rotem Schal") und filtere dann auf
  "Person erkennbar". Das ist eine Suche, kein Gesichtsabgleich: Rushbase identifiziert keine
  Personen anhand ihres Gesichts.
- **Sperren schützt deine Einträge:** Ist Material gesperrt, ändert die Analyse weder Personenangabe
  noch Einwilligung.
- **Mehrere auf einmal:** Wähle in der Bibliothek mehrere Materialien aus und drücke
  _Datenschutz_, um "Person erkennbar", Einwilligung und Nachweis für alle zu setzen. Im Reiter
  _Datenschutz_ eines einzelnen Materials bearbeitest du dieselben Felder samt Sperre.
- **Einen ganzen Batch oder eine Person sperren:** Öffne _Datenschutz_ in der Navigation. Wähle
  einen Batch oder tippe den Namen einer Person. Bei einem Namen findet Rushbase Material, das mit
  einem freigegebenen Wert des Taxonomie-Felds `person` (Wert, Bezeichnung oder Synonym) getaggt
  ist, sowie Material, das den Namen in Titel, Beschreibung oder Transkript nennt. Du siehst
  zuerst die Trefferzahl, sperrst dann alle und kannst im selben Schritt die Einwilligung samt
  Nachweis setzen. Gesichtserkennung ist standardmäßig aus und nicht umgesetzt: Es ist eine
  Textsuche, prüfe die Treffer.

### Löschen: der Papierkorb

- **Löschen** dürfen nur Administratoren in der Weboberfläche (nie über Claude oder die
  Companion-App). Öffne ein Material und drücke _In den Papierkorb_ (bei einem Duplikat: _Duplikat
  in den Papierkorb_) und bestätige den Dialog.
- Die Dateien (bei Kamerakarten der ganze Kartenordner, dazu die Sidecars) werden **verschoben**,
  nicht kopiert, nach `.rushbase/trash/<Asset-ID>/` mit ihrer Ordnerstruktur. Noch wird nichts
  gelöscht. Duplikate verlassen dabei `Input/`.
- _Verwaltung -> Papierkorb_ listet alles mit dem Datum der endgültigen Löschung (Standard 30
  Tage; die Frist stellst du dort ein). _Wiederherstellen_ legt die Dateien zurück; ist der
  ursprüngliche Pfad inzwischen belegt, nennt Rushbase ihn und verschiebt nichts.
- Abgelaufene Einträge löscht der Wartungslauf des Servers endgültig. Das ist die einzige Stelle in
  Rushbase, die Medien löscht, und jede Löschung steht im Audit-Log.

### Ton, der zu einem Video gehört

Überlappen sich die Datei eines Tonrekorders (BWF/iXML-Timecode) und ein Video desselben Batches
zeitlich, erscheint am Material der Reiter _Ton-Sync_ mit einem **Vorschlag**: Versatz, Sicherheit
und Grundlage (Timecode oder Aufnahmezeit). Verknüpft wird erst, wenn du _Bestätigen_ drückst;
_Ablehnen_ blendet ihn aus. Bestätigte Paare stehen in beiden Sidecars.

### Vorhandenes Material neu ablegen (optional)

_Verwaltung -> Migration_ ist standardmäßig aus. Eingeschaltet wählst du einen Batch und eine neue
Ablageregel, siehst eine Vorschau aller Verschiebungen und führst sie aus, indem du den angezeigten
Bestätigungstext tippst. Die Dateien werden per Rename verschoben, im Journal festgehalten, ihre
Sidecars ziehen mit, und du lädst eine **Relink-Liste** (CSV mit altem und neuem Pfad je
Betriebssystem) herunter, um die Clips in DaVinci Resolve neu zu verbinden. Claude kann keine
Migration starten.

## 10. Häufige Fragen und Probleme

**Meine Datei liegt noch in `Input/` und taucht nicht in der Bibliothek auf.**
Das ist normal, solange sie noch nicht analysiert wurde. Schau unter _Verarbeitung → Warteschlange_
nach: "Wartet" heißt, es fehlt ein laufender Worker - starte den Analyse-PC oder drücke in der
Companion-App **Verarbeiten**. Wurde gerade noch kopiert, wartet Rushbase einige Minuten, bis sich
die Datei nicht mehr ändert. Kamerakarten werden erst umsortiert, wenn die ganze Karte fertig ist.

**Wo ist mein Material nach dem Einsortieren?**
Unter `Content/` im Pfad `Medientyp/Jahr/Datum_Bezeichnung/`, also zum Beispiel
`Content/video/2026/2026-10-12_Imagefilm_Standort-A/`. Der genaue Pfad steht im Detail über
"Pfad kopieren". Die Dateinamen ändert Rushbase nie.

**Was bedeutet "Quarantäne"?**
Die Datei konnte nicht gelesen oder verarbeitet werden (zum Beispiel unvollständig kopiert oder
beschädigt). Sie bleibt unberührt in `Input/`. Unter _Verarbeitung → Quarantäne_ kannst du es mit
**Erneut versuchen** nochmal anstoßen, nachdem du die Datei neu kopiert hast. Hilft das nicht,
schau dir die Datei in einem Player an.

**Was heißt "Duplikat"?**
Rushbase hat dieselbe Datei (gleicher Inhalt) schon in der Bibliothek. Das Duplikat wird
**nicht** einsortiert und **nicht** gelöscht, es bleibt in `Input/`; die Übersicht zeigt, wie viel
Platz Duplikate belegen. Räumen kann ein Administrator oder du von Hand.

**Ich finde etwas nicht, obwohl es da sein müsste.**
Prüfe zuerst, ob Filter aktiv sind ("Zurücksetzen"). Dann ob das Material schon einsortiert ist
(Übersicht). Suche zusätzlich nach dem Dateinamen. Hat der Analyse-PC das Material noch nicht
gesehen, gibt es noch keine Beschreibung und kein Transkript - der Dateiname und die Stapel-Bezeichnung
sind aber sofort durchsuchbar.

**Die KI hat etwas Falsches getaggt.**
Tag entfernen oder ersetzen; die Änderung ist von Hand und bleibt. Für Material, das fertig ist,
zusätzlich **Sperren**.

**Kann ich Dateien in der Bibliothek umbenennen oder verschieben?**
Bitte nicht. Rushbase verlässt sich darauf, dass einsortierte Dateien dort bleiben, wo sie sind.
Organisiere stattdessen über Tags, Sammlungen und Filter.

**Wen frage ich bei Problemen?**
Deinen Administrator. Der findet unter _Verwaltung_ Benutzer, Worker, Backups und das Protokoll
(siehe [operations.md](../operations.md)).
