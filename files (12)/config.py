"""
Zentrale Konfiguration für das Wasserzähler-Auswertungsprojekt.
Alle Pfade und Parameter hier anpassen statt im Code zu suchen.
"""
import os

# --- Pfade ---
# Quellordner: hierhin werden die Fotos von der SD-Karte kopiert (kein Direktzugriff auf SD nötig)
RAW_PHOTOS_DIR = r"C:\Users\sgerh\Pictures\Lumix_Wasser\Reihe_2"

# Zwischenablage für verkleinerte/komprimierte Bilder, die an die API gehen
PROCESSED_DIR = r"C:\Users\sgerh\Pictures\Lumix_Wasser\processed"

# Ergebnis-CSV
OUTPUT_CSV = r"C:\Users\sgerh\Documents\Programming\Python\Wasseruhr\output\zaehlerstaende.csv"

# Log für übersprungene/fehlerhafte Bilder
ERROR_LOG = r"C:\Users\sgerh\Documents\Programming\Python\Wasseruhr\output\fehler.log"

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
CIRCLE_MIN_RADIUS_FRACTION = 0.08
CIRCLE_MAX_RADIUS_FRACTION = 0.30

# Fallback, falls in einer Reihe kein Kreis gefunden wird: kein Crop,
# ganzes Bild verwenden (lieber zu viel Kontext als den Zähler abschneiden).
CROP_FALLBACK_TO_FULL_IMAGE = True

# Zielkantenlänge (lange Seite) nach Resize, in Pixel.
# 1568px ist ein guter Kompromiss zwischen Lesbarkeit der Ziffern und Tokenkosten.
TARGET_LONG_EDGE = 1568

# JPEG-Qualität für die verarbeiteten Bilder (Claude-Input)
JPEG_QUALITY = 85

# --- Claude API ---
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# Bekannte Dateiendungen der Lumix-Kamera
VALID_EXTENSIONS = {".jpg", ".jpeg"}

os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
