# Wasserzähler-Auswertung

Automatisierte Überwachung eines Hauswasserzählers (Itron Aquadis,
Großzähler für 21 Wohneinheiten) per Kamera und Claude Vision API.

Hintergrund: auffälliger Verbrauchsanstieg von ~1000 auf ~3000 m³/Jahr,
Hausverwaltung hat Lecksuchdienst beauftragt. Die eigene Messung zeigt
stabil ~1200 m³/Jahr Hochrechnung und täglich 1,5–3,5h Nullverbrauch
nachts – kein Leckhinweis.

## Workflow-Übersicht

```
Kamera (10-Minuten-Intervall, Dateiname YYYYMMDD_HHMMSS.jpg)
    -> preprocess.py      Crop via meter_locator.py + Resize
    -> read_meter.py      Claude Vision API -> CSV, Plausibilitätsprüfung
    -> postprocess.py     Nachträgliche Konsistenzprüfung über die ganze CSV
    -> fetch_weather.py   Wetterdaten für Tagesübersicht (Open-Meteo, kein Key nötig)
    -> analyze.py         Zeitreihen-Plot + Anomalie-Check
    -> analyze_summary.py HTML-Zusammenfassung mit KPI-Kacheln für Hausverwaltung
```

Alle Skripte sind idempotent: bereits verarbeitete Fotos bzw. bereits
in der CSV vorhandene Einträge werden automatisch übersprungen.

## Ordnerstruktur

```
Wasseruhr/
  config.py
  meter_locator.py
  preprocess.py
  read_meter.py
  postprocess.py
  fetch_weather.py
  analyze.py
  analyze_summary.py
  .env                  # Standort-Koordinaten (nicht im Repo, siehe .env.example)
  .env.example          # Vorlage für .env
  output/
    zaehlerstaende.csv  # Hauptdatei: alle Messwerte
    wetter.csv          # gecachte Wetterdaten
    plot.png            # aktueller Zeitreihen-Plot
    zusammenfassung.html
```

Bilder liegen außerhalb des Projektordners:
```
C:\Users\...\Pictures\Lumix_Wasser\     # Originalfotos der Kamera
C:\Users\...\Pictures\Lumix_Wasser\processed\  # gecroppt + resized, von preprocess.py
```

## Setup

1. `.env` anlegen (Vorlage: `.env.example`):
   ```
   WETTER_LATITUDE=51.18
   WETTER_LONGITUDE=7.19
   ```

2. Pfade in `config.py` anpassen (`RAW_PHOTOS_DIR`, `PROCESSED_DIR`, `OUTPUT_CSV` etc.)

3. Python-Pakete installieren (empfohlen: `uv venv` + `uv pip install`):
   ```
   pip install pillow opencv-python-headless numpy anthropic pandas matplotlib plotly requests python-dotenv
   ```

4. Anthropic API-Key setzen (PowerShell):
   ```
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   Dauerhaft: `setx ANTHROPIC_API_KEY "sk-ant-..."` (neues Terminal danach nötig)

## Ablauf (normaler Betrieb)

```
python preprocess.py      # neue Fotos croppen + in /processed ablegen
python read_meter.py      # neue Bilder an Claude schicken, CSV erweitern
python postprocess.py     # Konsistenzprüfung über die ganze CSV
python fetch_weather.py   # Wetterdaten nachladen
python analyze.py         # Plot aktualisieren
python analyze_summary.py # HTML-Zusammenfassung erzeugen (output\zusammenfassung.html)
```

## Konfiguration (config.py)

| Parameter | Bedeutung |
|---|---|
| `MAX_ALTER_BILDER_TAGE` | Bilder älter als N Tage werden in read_meter.py übersprungen (normal: 2) |
| `MAX_RUECKGANG_M3` | Tolerierter Rückgang pro Messung (Leseunschärfe, z.B. 0.05) |
| `MAX_ANSTIEG_M3` | Maximaler Anstieg pro Messung (physikalisches Limit, z.B. 1.0) |
| `ZERO_CONSUMPTION_THRESHOLD_L_MIN` | Schwellwert für „kein Verbrauch" (l/min) |
| `DATENLUECKE_SCHWELLE_MINUTEN` | Ab wann eine Lücke als Datenlücke gilt |
| `APARTMENT_COUNT` | Anzahl Wohneinheiten (für Hochrechnung) |
| `REPORTED_YEARLY_M3` | Gemeldeter Jahresverbrauch (für Vergleich) |

## Plausibilitätsprüfung (zwei Stufen)

**Stufe 1 – Live in read_meter.py:**
Jeder neue Wert wird gegen seinen chronologischen Vorgänger geprüft
(nicht gegen den letzten CSV-Eintrag – das ist wichtig bei nachträglicher
Verarbeitung älterer Bilder). Zwei Grenzen:
- Rückgang > `MAX_RUECKGANG_M3` → UNPLAUSIBEL (Zähler läuft nicht rückwärts)
- Anstieg > `MAX_ANSTIEG_M3` → UNPLAUSIBEL (Ausreißer nach oben, z.B. Ziffer
  falsch erkannt); schützt alle Nachfolger vor falscher Referenz

**Stufe 2 – Nachträglich in postprocess.py:**
Betrachtet die gesamte Zeitreihe und arbeitet in drei Schritten:
1. Absolute Sicherheitsregel: Werte die mehr als 50% vom Gesamtmedian abweichen
2. Iterative Drei-Werte-Prüfung: Wert(N-1) ≤ Wert(N) ≤ Wert(N+1); pro Durchlauf
   wird nur der eine Kandidat mit der größten Abweichung entfernt
3. Randprüfung für ersten/letzten Wert der bereinigten Reihe

Durch Fehlerfortpflanzung fälschlich verworfene, eigentlich korrekte Werte
werden dabei wieder als plausibel erkannt.

## Bekannte Hinweise

**Datenlücken:** Wenn `MAX_ALTER_BILDER_TAGE` zu klein ist und Bilder im
`/processed`-Ordner liegen aber noch nicht in der CSV, werden sie beim
nächsten Lauf mit `MAX_ALTER_BILDER_TAGE = 999` nachverarbeitet.
Danach wieder auf 2 zurücksetzen.

**Crop:** Wird automatisch per Kreiserkennung bestimmt (`meter_locator.py`,
OpenCV Hough-Transformation). Bei jeder neuen Bildreihe wird ein
`_crop_preview.jpg` gespeichert – kurzer Blick empfohlen. Bei falscher
Erkennung `.crop_box.txt` manuell setzen (`left,top,right,bottom` in
Originalpixeln). Fallback auf volles Bild möglich (`CROP_FALLBACK_TO_FULL_IMAGE`).

**Duplikate:** `preprocess.py` erkennt Duplikate am Zeitstempel-Dateinamen
und überspringt sie. `postprocess.py` prüft zusätzlich auf identische
Zeitstempel in der CSV und behält bei Gruppen die Zeile mit bester Lesbarkeit.

**Nachtdefinition:** 01:00–05:59 Uhr in `analyze.py`, anpassbar.

**Grundlast-Schwellwert:** 0.05 l/min in `detect_anomalies()` – bei 21
Wohneinheiten ist „echtes Null" nachts unrealistisch (Kühlschränke etc.),
der Schwellwert sollte nach den ersten Wochen kalibriert werden.

## Hilfsscripts

| Script | Zweck |
|---|---|
| `fix_gap_27juni.py` | Einmaliger Reparatur-Script für Datenlücke 27.06.2026 |
| `remove_last_100.py` | Entfernt die letzten 100 Zeilen aus der CSV (Fehlerkorrektur) |

## Hardware

- **Kamera:** Lumix (10-Minuten-Intervall-Aufnahmen)
- **Geplant:** Raspberry Pi 4B (8GB) + Camera Module 3 im AP-Modus
  (SSID: `Wasserzaehler`) als dauerhafter Ersatz, da kein WLAN im Keller

## Dateien

| Datei | Beschreibung |
|---|---|
| `config.py` | Zentrale Pfade & Parameter |
| `meter_locator.py` | Automatische Zähler-Lokalisierung per OpenCV |
| `preprocess.py` | Bildvorverarbeitung (Crop, Resize, Komprimierung) |
| `read_meter.py` | Claude Vision API, Plausibilitätsprüfung, schreibt CSV |
| `postprocess.py` | Nachträgliche Konsistenzprüfung über die ganze CSV |
| `fetch_weather.py` | Wetterdaten von Open-Meteo, lokaler CSV-Cache |
| `analyze.py` | Zeitreihen-Plot, Anomalie-Heuristik |
| `analyze_summary.py` | HTML-Zusammenfassung mit KPI-Kacheln für Hausverwaltung |
| `.env.example` | Vorlage für `.env` (Standort-Koordinaten) |
