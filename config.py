"""
Zentrale Konfiguration für das Wasserzähler-Auswertungsprojekt.
Alle Pfade und Parameter hier anpassen statt im Code zu suchen.
"""
import os

# --- Pfade ---
# Quellordner: hierhin werden die Fotos von der SD-Karte kopiert (kein Direktzugriff auf SD nötig)
RAW_PHOTOS_DIR = r"C:\Users\sgerh\Pictures\Lumix_Wasser\raw_photos"

# Zwischenablage für verkleinerte/komprimierte Bilder, die an die API gehen
PROCESSED_DIR = r"C:\Users\sgerh\Pictures\Lumix_Wasser\processed"

# Zentraler Output-Ordner: ALLE erzeugten Dateien (CSV, Plots, Wetter-Cache,
# Fehler-Log) landen hier. Einmal hier anpassen (z.B. falls der Ordner mal
# verschoben wird), statt in jeder einzelnen Pfad-Variable einzeln.
OUTPUT_DIR = r"C:\Users\sgerh\Documents\Programming\Python\Wasseruhr\output"

# Ergebnis-CSV
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "zaehlerstaende.csv")

# Verbrauchs-Plot (von analyze.py erzeugt)
OUTPUT_PLOT = os.path.join(OUTPUT_DIR, "verbrauch.png")

# Tagesprofil-Balkendiagramm (von analyze.py erzeugt)
OUTPUT_HOURLY_PLOT = os.path.join(OUTPUT_DIR, "tagesprofil.png")

# 3D-Visualisierungen Tag x Stunde (von analyze_3d.py erzeugt, interaktives HTML)
OUTPUT_3D_SURFACE = os.path.join(OUTPUT_DIR, "verbrauch_3d_flaeche.html")
OUTPUT_3D_BARS = os.path.join(OUTPUT_DIR, "verbrauch_3d_balken.html")

# Log für übersprungene/fehlerhafte Bilder
ERROR_LOG = os.path.join(OUTPUT_DIR, "fehler.log")

# --- Bildverarbeitung ---
# NEU: Bilder werden jetzt in Unterordnern pro Bildreihe abgelegt, z.B.:
#   raw_photos/reihe_2026-06-17/P1050965.JPG
#   raw_photos/reihe_2026-06-20/P1060001.JPG
# Innerhalb einer Reihe steht die Kamera (im Rahmen des Stativ-Wackelspielraums)
# konstant, daher wird der Crop-Bereich EINMAL pro Reihe automatisch per
# Kreiserkennung (OpenCV) bestimmt und für alle Fotos der Reihe wiederverwendet.

# Auflösung, auf die für die Kreiserkennung herunterskaliert wird (nur für die
# Erkennung selbst, NICHT für das gespeicherte Endbild -- spart Rechenzeit,
# da HoughCircles auf der vollen 5472x3648-Auflösung sehr langsam ist).
CIRCLE_DETECT_WIDTH = 800

# Wie viel größer als der erkannte Zähler-Radius die Crop-Box sein soll.
# 1.6 hat sich in Tests als guter Wert erwiesen: ganzer Zähler inkl. Klappdeckel
# sichtbar, aber Hintergrund (Fahrrad, Rohre) größtenteils draußen.
CROP_MARGIN_FACTOR = 1.6

# Geschätzter Anteil der Bildbreite, den der Zähler-Kreis einnehmen kann
# (je nach Kameraabstand). Grenzt den Suchraum für HoughCircles ein.
# Werte aus echten Testfotos kalibriert: bei größerem Kameraabstand nimmt der
# Zähler ca. 9% der Bildbreite ein, bei näherer Position bis ca. 20%.
# WICHTIG: Erkennung ist trotzdem nicht in jedem Lichtverhältnis zuverlässig --
# siehe manueller Override in meter_locator.py / get_crop_box_for_series().
CIRCLE_MIN_RADIUS_FRACTION = 0.07
CIRCLE_MAX_RADIUS_FRACTION = 0.22

# Fallback, falls in einer Reihe kein Kreis gefunden wird: kein Crop,
# ganzes Bild verwenden (lieber zu viel Kontext als den Zähler abschneiden).
CROP_FALLBACK_TO_FULL_IMAGE = True

# Zielkantenlänge (lange Seite) nach Resize, in Pixel.
# 1568px ist ein guter Kompromiss zwischen Lesbarkeit der Ziffern und Tokenkosten.
TARGET_LONG_EDGE = 1568

# JPEG-Qualität für die verarbeiteten Bilder (Claude-Input)
JPEG_QUALITY = 85

# Bekannte Dateiendungen der Lumix-Kamera -- alles andere in einem
# Reihen-Ordner (z.B. Crop-Vorschaubilder, .done-Marker) wird ignoriert.
VALID_EXTENSIONS = {".jpg", ".jpeg"}

# --- Claude API ---
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# --- Wetter (fetch_weather.py / analyze.py) ---
# Koordinaten von Remscheid für die Wetterabfrage bei Open-Meteo (kostenlos,
# kein API-Key nötig). Quelle: https://open-meteo.com/en/docs/historical-weather-api
from dotenv import load_dotenv
 
load_dotenv()  # liest .env aus dem Projektverzeichnis
 
WETTER_LATITUDE  = float(os.getenv("WETTER_LATITUDE",  "51.18"))
WETTER_LONGITUDE = float(os.getenv("WETTER_LONGITUDE", "7.19"))
WETTER_CSV = os.path.join(OUTPUT_DIR, "wetter.csv")

# --- Auswertung (analyze.py) ---
# Seriennummer des überwachten Wasserzählers (Itron Aquadis), wird als
# Beschriftung in den Plot-Titeln verwendet -- rein informativ, keine
# funktionale Bedeutung. Steht auf dem Zähler-Gehäuse und im Barcode-Bereich.
METER_SERIAL_NUMBER = "8ITR0100426227"

# Schwelle, unterhalb der ein Zeitintervall als "kein Wasser entnommen" gilt.
# Bewusst nicht exakt 0.0, um die übliche Restungenauigkeit (Modellvarianz bei
# der letzten Ziffernrolle, siehe README) zu tolerieren, ohne diese Mini-
# Schwankungen fälschlich als "es wurde doch Wasser entnommen" zu werten.
ZERO_CONSUMPTION_THRESHOLD_L_MIN = 0.1

# Plausibilitätsgrenzen für read_meter.py
MAX_RUECKGANG_M3 = 0.05   # tolerierter Rückgang pro Messung (Leseunschärfe)
MAX_ANSTIEG_M3   = 1.0    # maximaler Anstieg pro Messung (physikalisches Limit)

# Ab welcher zeitlichen Lücke zwischen zwei aufeinanderfolgenden Messpunkten
# ein Intervall als "Datenlücke" gekennzeichnet wird (z.B. wenn der Kamera-
# Speicher voll war oder die SD-Karte zwischenzeitlich nicht lief). Normale
# Intervalle liegen bei ca. 10 Minuten; deutlich größere Lücken liefern einen
# nur über die Lücke GEMITTELTEN Verbrauchswert, der echte Schwankungen
# innerhalb der Lücke (Spitzen wie auch Nullphasen) verschleiert -- siehe
# README. Der Wert selbst wird NICHT verändert, nur im Plot visuell markiert.
DATENLUECKE_SCHWELLE_MINUTEN = 30

MINDESTTAGE_BALKENDIAGRAMM = 4

MAX_ALTER_BILDER_TAGE = 2

APARTMENT_COUNT     = 21      # Anzahl Wohneinheiten
REPORTED_YEARLY_M3  = 3000    # Vom Versorger gemeldeter Jahresverbrauch

os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)