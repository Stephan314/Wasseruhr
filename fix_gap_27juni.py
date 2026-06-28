"""
fix_gap_27juni.py
-----------------
Benennt die _1-Suffixdateien des 27.06.2026 ab 08:02 Uhr in /processed
zurück in ihre Originalnamen, damit read_meter.py sie erneut verarbeitet.

Ablauf:
  1. Sucht in /processed alle Dateien der Form 20260627_HHMMSS_1.jpg
     mit Uhrzeit >= 080000
  2. Benennt sie zurück zu 20260627_HHMMSS.jpg
  3. Gibt eine Zusammenfassung aus

Danach:
  - MAX_ALTER_BILDER_TAGE in config.py kurz auf 999 setzen
  - Pipeline ab read_meter.py neu starten
  - Danach MAX_ALTER_BILDER_TAGE wieder auf 2 zurücksetzen
"""

import re
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────
PROCESSED_DIR = Path(r"C:\Users\sgerh\Pictures\Lumix_Wasser\processed")

# Nur Dateien ab dieser Uhrzeit zurückbenennen (bereits verarbeitet bis 075242)
START_TIME = "080000"

# Datum das wir reparieren
TARGET_DATE = "20260627"
# ──────────────────────────────────────────────────────────────────────────────

pattern = re.compile(rf"^({TARGET_DATE}_(\d{{6}}))_1\.jpg$", re.IGNORECASE)

candidates = []
for f in sorted(PROCESSED_DIR.glob(f"{TARGET_DATE}_*_1.jpg")):
    m = pattern.match(f.name)
    if m:
        time_str = m.group(2)
        if time_str >= START_TIME:
            candidates.append((f, f.parent / f"{m.group(1)}.jpg"))

if not candidates:
    print("Keine passenden Dateien gefunden.")
    print(f"Gesucht in: {PROCESSED_DIR}")
    print(f"Muster:     {TARGET_DATE}_HHMMSS_1.jpg  mit Uhrzeit >= {START_TIME}")
else:
    print(f"{len(candidates)} Dateien gefunden zum Zurückbenennen:\n")
    for src, dst in candidates[:5]:
        print(f"  {src.name}  →  {dst.name}")
    if len(candidates) > 5:
        print(f"  ... und {len(candidates) - 5} weitere")

    antwort = input(f"\nAlle {len(candidates)} Dateien zurückbenennen? (j/n): ").strip().lower()
    if antwort == "j":
        ok = 0
        fehler = 0
        for src, dst in candidates:
            if dst.exists():
                print(f"  SKIP (Ziel existiert bereits): {dst.name}")
                fehler += 1
            else:
                src.rename(dst)
                ok += 1
        print(f"\nFertig: {ok} umbenannt, {fehler} übersprungen.")
        print("\nNächste Schritte:")
        print("  1. In config.py:  MAX_ALTER_BILDER_TAGE = 999")
        print("  2. Pipeline starten ab read_meter.py")
        print("  3. Nach erfolgreichem Lauf: MAX_ALTER_BILDER_TAGE = 2  (zurücksetzen!)")
    else:
        print("Abgebrochen, keine Dateien wurden geändert.")
