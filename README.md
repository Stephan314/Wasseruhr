# Wasserzähler-Auswertung (Lumix-Workflow)

## Workflow-Übersicht

```
SD-Karte (Lumix)
    -> manuell kopieren nach RAW_PHOTOS_DIR/reihe_<name>/   (ein Unterordner pro Bildreihe!)
    -> preprocess.py    (Crop via meter_locator.py + Resize, Pillow/OpenCV statt GIMP)
    -> read_meter.py    (Claude Vision API -> CSV, inkl. Live-Plausibilitätsprüfung)
    -> postprocess.py   (Nachträgliche Drei-Werte-Konsistenzprüfung über die ganze CSV)
    -> analyze.py       (Plot + Anomalie-Check)
```

## Ordnerstruktur für Fotos

Da sich die Kameraposition zwischen Akkuwechseln leicht verschieben kann (Stativ
ist nicht fixiert), braucht jede Bildreihe ihren eigenen Unterordner:

```
raw_photos/
  reihe_2026-06-17/
    P1050965.JPG
    P1050966.JPG
    ...
  reihe_2026-06-20/
    P1060027.JPG
    ...
```

`preprocess.py` bestimmt den Crop-Bereich automatisch per Kreiserkennung
(`meter_locator.py`, OpenCV Hough-Transformation) einmalig anhand des ersten
Fotos jeder Reihe und wendet ihn auf alle weiteren Fotos derselben Reihe an.
Der Ordnername selbst ist frei wählbar, wichtig ist nur die Unterordner-Ebene.

## Setup

1. Pfade in `config.py` an dein System anpassen (RAW_PHOTOS_DIR etc.).
2. Python-Pakete installieren:
   ```
   pip install pillow opencv-python-headless numpy anthropic pandas matplotlib plotly requests
   ```
3. API-Key setzen (PowerShell):
   ```
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Für dauerhafte Einrichtung: `setx ANTHROPIC_API_KEY "sk-ant-..."` (neues Terminal nötig danach).

## Ablauf nach jedem SD-Kartenwechsel

```
python preprocess.py     # neue Fotos verkleinern/croppen
python read_meter.py     # neue Bilder an Claude schicken, CSV erweitern
python postprocess.py    # Drei-Werte-Konsistenzprüfung über die ganze CSV
python fetch_weather.py  # optional: Wetterdaten für die Tagesübersicht nachladen (braucht Internet)
python analyze.py        # Plot aktualisieren, Anomalien checken
python analyze_3d.py     # optional: interaktive 3D-Ansicht (Tag x Stunde) aktualisieren
```

Alle fünf Skripte sind idempotent: bereits verarbeitete Fotos bzw. bereits
in der CSV vorhandene Bilder werden automatisch übersprungen. Mehrfaches
Ausführen ist also unkritisch. `postprocess.py` kann beliebig oft erneut
laufen (legt vor jeder Änderung ein `.bak`-Backup der CSV an); bei einer
bereits konsistenten CSV ändert es nichts.

## Bekannte Annahmen / TODOs

- **Tagesverbrauch-Box im Verbrauchsraten-Plot**: zeigt pro Kalendertag den
  Gesamtverbrauch (m³) neben der Nullverbrauchsdauer. WICHTIG bei
  unvollständigen Randtagen (z.B. der allererste Tag, falls die Messung erst
  abends beginnt, oder der letzte Tag, falls sie morgens endet): der
  angezeigte Wert ist nur der TATSÄCHLICH GEMESSENE Anteil dieses Tages,
  keine Hochrechnung auf 24 Stunden -- ein Randtag mit nur 0,5 m³ bedeutet
  also nicht zwingend einen niedrigeren Verbrauch, sondern oft nur weniger
  erfasste Stunden. Größere Datenlücken werden dabei zeitanteilig auf die
  überspannten Tage verteilt (siehe compute_daily_consumption_summary).

- **Interpolierte Anzeige bei Datenlücken**: im Verbrauchsraten-Plot wird die
  rote Linie für die ANZEIGE (nicht für Berechnungen) bei größeren
  Datenlücken zusätzlich an stündlichen Zwischenpunkten gezeichnet, mit dem
  jeweils zeitanteiligen Verbrauch -- das ergibt einen sanfteren Übergang
  statt einer einzelnen, künstlich flachen Strecke über die gesamte Lücke.
  Wie immer gilt: diese Zwischenpunkte sind eine Annahme (Gleichverteilung
  über die Lücke), kein echter Messwert.

- **Duplikate durch nicht gelöschte SD-Karten-Fotos**: PRAXISFALL (18.06.2026):
  Wenn Fotos nach der Übertragung nicht von der SD-Karte gelöscht werden,
  können sie beim nächsten Kopiervorgang versehentlich erneut in einen neuen
  Bildreihen-Ordner gelangen und so doppelt verarbeitet werden. Da Claude
  beim zweiten Lesen desselben Fotos nicht zwingend exakt denselben Wert
  liefert (geringe Modellvarianz bei unscharfen letzten Ziffern), entstehen
  dadurch viele kleine, fälschliche Monotonie-Verstöße in der CSV, die
  `postprocess.py` als zusammenhängenden Block von Ausreißern interpretiert
  -- das führte praktisch zu großen Lücken im Verbrauchsplot.
  LÖSUNG: `preprocess.py` erkennt jetzt Duplikate direkt anhand des
  EXIF-Zeitstempels: existiert im PROCESSED_DIR bereits eine Datei mit
  demselben Zeitstempel-Namen, wird das neue Foto als Duplikat übersprungen
  (Konsolenhinweis "DUPLIKAT übersprungen"), statt wie früher mit einem
  Suffix (`_1`, `_2`) als vermeintlich neues Foto gespeichert zu werden --
  bei 10-Minuten-Intervallaufnahmen ist ein identischer Sekunden-Zeitstempel
  praktisch immer ein echtes Duplikat, kein Kollisionsfall.
  EMPFEHLUNG: Trotzdem nach jeder SD-Kartenentnahme die übertragenen Fotos
  von der Kamera löschen, um Duplikate von vornherein zu vermeiden.

- **Plausibilitätsprüfung, zwei Stufen**: `read_meter.py` prüft LIVE jeden
  neuen Wert gegen den zuletzt als plausibel markierten Wert (Monotonie:
  der Zähler kann nicht rückwärts laufen). PRAXISERFAHRUNG (16./17.06.2026):
  diese Live-Prüfung erkennt nur Werte, die fälschlich zu NIEDRIG gelesen
  wurden, nicht aber Werte, die fälschlich zu HOCH gelesen wurden (z.B. eine
  Ziffernrolle, die im Übergang als "9" statt "6" gelesen wird). Schlimmer:
  ein solcher zu hoher Ausreißer wird live als neuer Referenzwert übernommen,
  wodurch ALLE folgenden, eigentlich korrekten Werte fälschlich als
  unplausibel markiert werden (Fehlerfortpflanzung) -- in der Praxis führte
  ein einzelner Lesefehler so dazu, dass rund 10 nachfolgende korrekte Werte
  fälschlich verworfen wurden.
  LÖSUNG: `postprocess.py` betrachtet die GESAMTE Zeitreihe im Nachhinein
  (nicht sequentiell) und arbeitet in drei Schritten: (1) eine ABSOLUTE
  Sicherheitsregel sortiert Werte aus, die mehr als 50% vom Gesamtmedian
  aller Werte abweichen (fängt z.B. exakt 0.0 ab -- trat in der Praxis bei
  einem komplett schwarzen, fehlgeschlagenen Foto auf, für das Claude einen
  Platzhalterwert statt eines echten Fehlers lieferte); (2) eine ITERATIVE
  Prüfung, ob jeder verbleibende Wert zwischen seinem zeitlichen Vorgänger
  und Nachfolger liegt (Wert(N-1) <= Wert(N) <= Wert(N+1), da der Zähler nur
  vorwärts läuft) -- WICHTIG dabei: pro Durchlauf wird nur der EINE
  Kandidat mit der größten Abweichung entfernt, nicht alle gleichzeitig,
  da sonst zwei direkt benachbarte, eigenständige kleine Abweichungen sich
  gegenseitig fälschlich als Ausreißer "anstecken" können; (3) eine
  Randprüfung für den allerersten/-letzten Wert der bereinigten Reihe
  (relativ zum typischen Sprung in der Reihe, kein fester Schwellwert).
  Dabei werden ALLE Zeilen neu bewertet, auch die schon von read_meter.py
  als unplausibel markierten -- durch Fehlerfortpflanzung fälschlich
  verworfene, eigentlich korrekte Werte werden so wieder als plausibel
  erkannt.
  EINSCHRÄNKUNG: Minimale Monotonie-Verletzungen im Bereich von wenigen
  Millilitern (z.B. 0,002-0,015 m³, durch die übliche Unschärfe an der
  letzten Ziffernrolle im Übergang) werden bewusst NICHT automatisch
  korrigiert -- das Risiko, dabei auch legitime kleine Verbrauchsschwankungen
  fälschlich zu verwerfen, wäre höher als der Genauigkeitsgewinn. Falls
  mehrere Werte HINTEREINANDER falsch sind (nicht durch Schritt 0 erfasst),
  kann auch die iterative Prüfung das nicht immer vollständig auflösen.

- **Duplikate durch nicht gelöschte SD-Karten-Fotos, zweite Verteidigungslinie**:
  Auch wenn `preprocess.py` neue Duplikate jetzt direkt verhindert (siehe
  oben), prüft `postprocess.py` zusätzlich als ERSTEN Schritt (noch vor der
  Drei-Werte-Logik), ob in der CSV mehrere Zeilen mit EXAKT IDENTISCHEM
  Zeitstempel existieren -- das fängt auch Altlasten aus früheren Läufen ab
  (bevor preprocess.py die Duplikat-Erkennung hatte) und Fälle, in denen das
  Foto über einen separaten Weg doppelt in die CSV gelangt ist.
  WICHTIG: nur ein doppelter ZEITSTEMPEL wird angefasst, nicht doppelte
  WERTE -- zwei unterschiedliche Zeitpunkte mit demselben Zählerstand sind
  völlig normal (z.B. nachts ohne Verbrauch) und werden nicht verändert.
  Bei einer Duplikat-Gruppe wird die Zeile mit der besten `lesbarkeit` (gut
  > mittel > schlecht) behalten, der Rest wird KOMPLETT aus der CSV entfernt
  (nicht nur als unplausibel markiert, da es echte Datenredundanz ist, kein
  fragwürdiger Messwert). PRAXISFALL (18.06.2026): 82 von 287 Zeilen waren
  Duplikate (selbes Foto zweimal verarbeitet, da nicht von der SD-Karte
  gelöscht); davon hatten 16 Gruppen sogar leicht UNTERSCHIEDLICHE Werte
  (Modellvarianz bei Claude bei zwei separaten Lesungen desselben Fotos),
  was vorher zu langen, fälschlichen Ausreißer-Ketten über mehrere Stunden
  führte (sichtbar als große Lücken im Verbrauchsplot). Nach Einführung
  dieser Regel reduzierte sich die größte verbleibende Datenlücke von
  mehreren Stunden auf 30 Minuten.
- **Crop**: Wird automatisch pro Bildreihe per Kreiserkennung bestimmt (siehe
  oben). PRAXISERFAHRUNG: Die automatische Erkennung ist nicht in jedem
  Lichtverhältnis hundertprozentig zuverlässig -- bei einem Testfoto wurde
  zunächst ein viel zu großer, falsch positionierter Kreis gewählt, weil die
  Wand im Hintergrund ähnlich hell war wie der Zähler. Der Radius-Suchbereich
  in `config.py` (`CIRCLE_MIN/MAX_RADIUS_FRACTION`) wurde daraufhin anhand
  echter Testfotos enger kalibriert, was das Problem in dem konkreten Fall
  behoben hat.
  Als zusätzliches Sicherheitsnetz speichert `preprocess.py` bei JEDER neuen
  Bildreihe automatisch ein Vorschaubild `_crop_preview.jpg` im jeweiligen
  Reihen-Unterordner (grünes Rechteck = erkannte Crop-Box auf dem verkleinerten
  Originalfoto). Nach dem ersten Lauf einer neuen Reihe lohnt sich ein kurzer
  Blick in dieses Bild. Falls die Box offensichtlich falsch liegt, die Datei
  `.crop_box.txt` im selben Ordner von Hand mit eigenen Werten überschreiben
  (Format: `left,top,right,bottom` in Original-Pixelkoordinaten, z.B.
  `1852,1196,3309,2653`) und das Skript erneut starten -- der manuelle Wert
  wird dann übernommen statt neu zu rechnen.
  Falls gar kein Kreis erkannt wird, fällt die Pipeline automatisch auf "kein
  Crop" zurück (`CROP_FALLBACK_TO_FULL_IMAGE = True`).
- **Zähler-Bauform**: Ziffernrollen (m³) + Zeigerrad (Bruchteile). Der Prompt
  in `read_meter.py` ist darauf ausgelegt, beide Teile zu kombinieren. Sollte
  Claude beim Zeiger systematisch danebenliegen, ggf. nur den Ziffernwert für
  die Verbrauchsanalyse nutzen (Trend ist ohnehin wichtiger als Präzision).
- **Nachtdefinition**: aktuell 01:00–05:59 Uhr in `analyze.py`, anpassbar.
- **Grundlast-Schwellwert**: aktuell 0.05 l/min in `detect_anomalies()`,
  sollte nach den ersten echten Daten kalibriert werden (abhängig vom
  Hintergrundverbrauch von 21 Wohnungen, z.B. durch Kühlschränke/Heizungen
  mit Wasseranschluss etc. -- "echtes Null" ist bei 21 Einheiten unrealistisch).

## Dateien

- `config.py` – zentrale Pfade & Parameter
- `meter_locator.py` – automatische Zähler-Lokalisierung per OpenCV-Kreiserkennung
- `preprocess.py` – Bildvorverarbeitung (Crop pro Bildreihe, Resize, Komprimierung)
- `read_meter.py` – Claude Vision API Aufruf, Live-Plausibilitätsprüfung, schreibt CSV
- `postprocess.py` – nachträgliche Drei-Werte-Konsistenzprüfung über die ganze CSV
- `fetch_weather.py` – ruft historische Tageswetterdaten (Open-Meteo, kein API-Key
  nötig) für Remscheid ab und cached sie lokal in wetter.csv, für die Wetterspalte
  in der Tagesübersicht-Box von analyze.py
- `analyze.py` – Pandas/Matplotlib Auswertung & Anomalie-Heuristik (Zeitreihen-Plot
  mit Nullverbrauchs-/Nacht-/Datenlücken-Hervorhebung + Tages-Zusammenfassung in
  der Legende, stündliches Tagesprofil-Balkendiagramm gemittelt über alle Tage)
- `analyze_3d.py` – interaktive 3D-Visualisierung (Plotly, HTML) des Verbrauchs
  über Tag x Stunde, als Fläche und als Balkendiagramm (siehe unten)