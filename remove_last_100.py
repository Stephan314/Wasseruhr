"""
remove_last_100.py
------------------
Entfernt die letzten 100 Datenzeilen aus zaehlerstaende.csv
(die fehlerhaften UNPLAUSIBEL-Einträge vom letzten read_meter.py-Lauf).

Zeigt vorher zur Kontrolle die ersten und letzten betroffenen Zeilen an
und fragt vor dem Schreiben nach Bestätigung.
"""

from pathlib import Path
import config

CSV_PATH = Path(config.OUTPUT_CSV)
REMOVE_COUNT = 100

# CSV einlesen
with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    lines = f.readlines()

header = lines[0]
data_lines = lines[1:]

if len(data_lines) < REMOVE_COUNT:
    print(f"FEHLER: CSV hat nur {len(data_lines)} Datenzeilen, kann keine {REMOVE_COUNT} entfernen.")
    exit(1)

keep   = data_lines[:-REMOVE_COUNT]
remove = data_lines[-REMOVE_COUNT:]

print(f"CSV enthält {len(data_lines)} Datenzeilen.")
print(f"Zu entfernende Zeilen: {REMOVE_COUNT}")
print(f"Verbleibende Zeilen:   {len(keep)}")
print()
print("Erste zu löschende Zeile:")
print(" ", remove[0].strip())
print("Letzte zu löschende Zeile:")
print(" ", remove[-1].strip())
print()
print("Letzte verbleibende Zeile nach dem Löschen:")
print(" ", keep[-1].strip())
print()

antwort = input(f"Letzte {REMOVE_COUNT} Zeilen unwiderruflich löschen? (j/n): ").strip().lower()
if antwort == "j":
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(header)
        f.writelines(keep)
    print(f"\nFertig. {REMOVE_COUNT} Zeilen entfernt. CSV hat jetzt {len(keep)} Datenzeilen.")
else:
    print("Abgebrochen, CSV unverändert.")
