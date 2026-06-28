"""
Nachbearbeitung der Wasserzähler-CSV: Erkennt einzelne Lesefehler robuster,
als es während des Live-Durchlaufs in read_meter.py möglich ist.

HINTERGRUND: read_meter.py prüft live nur "ist der neue Wert >= dem letzten
ALS PLAUSIBEL bekannten Wert?". Das erkennt Werte, die zu NIEDRIG sind, aber
NICHT Werte, die fälschlich zu HOCH gelesen wurden (z.B. eine Ziffernrolle,
die als "9" statt "6" gelesen wird). Schlimmer noch: ein solcher zu hoher
Fehlwert wird live als neuer Referenzwert übernommen, wodurch ALLE folgenden,
eigentlich korrekten Werte fälschlich als "zu niedrig" / unplausibel markiert
werden (Fehlerfortpflanzung). Genau dieses Muster trat in der Praxis auf
(16./17.06.2026): ein einzelner Ausreißer nach oben führte dazu, dass ca.
10 nachfolgende, korrekte Werte fälschlich als unplausibel markiert wurden.

LÖSUNG: Dieses Skript betrachtet die GESAMTE Zeitreihe im Nachhinein (nicht
sequentiell wie read_meter.py) und prüft für jeden Wert N, ob er zwischen
seinem zeitlichen Vorgänger (N-1) und Nachfolger (N+1) liegt -- der Zähler
kann nur vorwärts laufen, also muss bei drei korrekten Messungen gelten:
Wert(N-1) <= Wert(N) <= Wert(N+1). Dabei werden ALLE Zeilen mit einem
gültigen Zahlenwert neu bewertet, auch die, die read_meter.py schon als
"nein" markiert hatte -- so werden durch Fehlerfortpflanzung fälschlich
verworfene, eigentlich korrekte Werte wieder als plausibel erkannt.

Überschreibt NICHT direkt: legt zuerst ein Backup der Original-CSV an
(*.bak), dann wird die CSV mit den korrigierten plausibel/hinweis-Spalten
neu geschrieben.

Aufruf: python postprocess.py
"""
import csv
import shutil
from datetime import datetime

import config


def load_all_rows():
    """Liest ALLE Zeilen aus der CSV (unabhängig vom bisherigen plausibel-Wert)
    und parst Zeitstempel/Wert, wo möglich. Zeilen ohne gültigen Zahlenwert
    (z.B. echte API-Fehler) bekommen _wert=None und werden bei der Drei-Werte-
    Prüfung übersprungen, bleiben aber in der CSV unverändert erhalten."""
    with open(config.OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    for row in rows:
        try:
            row["_wert"] = float(row["gesamtwert_m3"])
        except (ValueError, KeyError):
            row["_wert"] = None
        try:
            row["_zeit"] = datetime.fromisoformat(row["zeitstempel"])
        except (ValueError, KeyError):
            row["_zeit"] = None

    rows.sort(key=lambda r: r["_zeit"] or datetime.min)
    return rows, fieldnames


def remove_duplicate_timestamps(rows: list) -> tuple:
    """Erkennt Zeilen mit IDENTISCHEM Zeitstempel und entfernt alle außer
    einer pro Gruppe. Hintergrund: bei 10-Minuten-Intervallaufnahmen ist ein
    doppelter Sekunden-Zeitstempel technisch unmöglich für zwei echte,
    unterschiedliche Fotos -- er entsteht in der Praxis dadurch, dass
    dasselbe Foto zweimal verarbeitet wurde (z.B. weil es nach der
    Übertragung nicht von der SD-Karte gelöscht und beim nächsten
    Kopiervorgang erneut in einen Bildreihen-Ordner gelangt ist).

    WICHTIG -- Unterscheidung zu erlaubten Wert-Duplikaten: zwei
    UNTERSCHIEDLICHE Zeitpunkte dürfen durchaus denselben Zählerstand zeigen
    (z.B. nachts, wenn kein Wasser verbraucht wird) -- das ist normal und
    wird hier nicht angefasst. Nur ein doppelter ZEITSTEMPEL ist verdächtig.

    Auswahl bei Duplikaten: die Zeile mit der besten 'lesbarkeit' (gut >
    mittel > schlecht) wird behalten, der Rest wird komplett aus der Liste
    entfernt (nicht nur als unplausibel markiert, da es sich um echte
    Datenredundanz handelt, nicht um einen fragwürdigen Messwert).

    Gibt (bereinigte_rows, anzahl_entfernt) zurück."""
    lesbarkeit_rang = {"gut": 0, "mittel": 1, "schlecht": 2}

    from collections import defaultdict
    by_zeit = defaultdict(list)
    for row in rows:
        by_zeit[row["zeitstempel"]].append(row)

    bereinigt = []
    entfernt = 0
    entfernte_details = []

    for zeitstempel, gruppe in by_zeit.items():
        if len(gruppe) == 1:
            bereinigt.append(gruppe[0])
            continue

        # Mehrere Zeilen mit demselben Zeitstempel: die mit bester
        # lesbarkeit behalten, Rest verwerfen. Bei Gleichstand gewinnt die
        # erste in der ursprünglichen Reihenfolge (stabil durch sort-Key).
        gruppe_sortiert = sorted(
            gruppe, key=lambda r: lesbarkeit_rang.get(r.get("lesbarkeit", ""), 99)
        )
        behalten = gruppe_sortiert[0]
        bereinigt.append(behalten)

        for verworfen in gruppe_sortiert[1:]:
            entfernt += 1
            entfernte_details.append((zeitstempel, verworfen["dateiname"], verworfen.get("_wert")))

    bereinigt.sort(key=lambda r: r["_zeit"] or datetime.min)
    return bereinigt, entfernt, entfernte_details


def find_rate_outliers(rows: list, window: int = 6, deviation_factor: float = 8.0) -> set:
    """Erkennt einzelne Werte (oder ganze Serien von Werten), die weit von
    ihrer lokalen Umgebung abweichen -- auch wenn sie im Verhältnis zu ihrem
    direkten Nachbarn (Drei-Werte-Prüfung in find_outliers) "passen", weil
    sie Teil einer in sich konsistenten Fehlserie sind.

    HINTERGRUND (Praxisfall 21.06.2026): eine ganze Serie von Lesefehlern
    (Claude Vision verwechselt über mehrere Stunden hinweg systematisch die
    ersten Ziffern, z.B. 08162 -> 08619, vermutlich wegen eines
    Lichtreflexes) kann die Drei-Werte-Prüfung durchrutschen, weil jeder
    Fehlwert im Verhältnis zu seinem ebenfalls falschen Nachbarn "passt".
    Auch die absolute Sicherheitsregel (Abweichung vom GESAMTmedian) greift
    nicht zuverlässig, weil ~450 m³ Differenz bei einem Gesamtstand von
    ~8000 m³ unter deren 50%-Schwelle bleibt.

    METHODE: für jeden Wert wird der Median eines lokalen Zeitfensters
    (window Werte vor und nach der Stelle, OHNE den Wert selbst) als
    Referenz berechnet. Liegt der Wert mehr als deviation_factor mal den
    TYPISCHEN lokalen Sprung von diesem Referenzwert entfernt, gilt er als
    Ausreißer. Das erkennt -- im Gegensatz zu einem reinen Sprungvergleich
    mit dem direkten Nachbarn -- zuverlässig, WELCHER der beiden an einem
    Übergang beteiligten Werte der eigentliche Ausreißer ist, egal ob eine
    Fehlserie beginnt oder endet, weil die Mehrheit der umliegenden Werte
    auf der "richtigen" Seite liegt."""
    valid = [r for r in rows if r["_wert"] is not None]
    n = len(valid)
    if n < 2 * window + 3:
        return set()  # zu wenige Werte für ein verlässliches lokales Fenster

    werte = [r["_wert"] for r in valid]

    # Typischer Sprung zwischen direkt benachbarten Werten (Median über die
    # ganze Reihe), als Maßstab dafür, wie groß eine "normale" Abweichung ist.
    diffs = [abs(werte[i + 1] - werte[i]) for i in range(n - 1)]
    diffs_sorted = sorted(diffs)
    m = len(diffs_sorted)
    typical_diff = diffs_sorted[m // 2] if m % 2 == 1 else (diffs_sorted[m // 2 - 1] + diffs_sorted[m // 2]) / 2
    schwelle = max(typical_diff * deviation_factor, 0.01)

    outlier_filenames = set()
    for i in range(n):
        lokale_nachbarn = werte[max(0, i - window):i] + werte[i + 1:i + 1 + window]
        if len(lokale_nachbarn) < window:  # am Rand: kleineres Fenster akzeptieren, aber nicht zu klein
            continue
        lokale_sorted = sorted(lokale_nachbarn)
        k = len(lokale_sorted)
        lokaler_median = lokale_sorted[k // 2] if k % 2 == 1 else (lokale_sorted[k // 2 - 1] + lokale_sorted[k // 2]) / 2

        if abs(werte[i] - lokaler_median) > schwelle:
            outlier_filenames.add(valid[i]["dateiname"])

    return outlier_filenames


def find_outliers(rows: list, edge_factor: float = 10.0, absolute_factor: float = 0.5) -> set:
    """Geht die zeitlich sortierte Liste durch (nur Zeilen mit gültigem Wert)
    und markiert Dateinamen, deren Wert nicht zwischen Vorgänger und
    Nachfolger liegt, OBWOHL Vorgänger und Nachfolger selbst eine plausible
    (steigende) Beziehung zueinander haben.

    SCHRITT 0 -- absolute Sicherheitsregel (unabhängig von der Position in
    der Reihe): Werte, die um mehr als `absolute_factor` (Standard: 50%) vom
    GESAMTMEDIAN aller Werte abweichen, gelten als grobe Fehlmessung und
    werden direkt aussortiert, BEVOR die Nachbarschaftsprüfung beginnt. Das
    fängt z.B. Werte wie 0.0 ab (komplett schwarzes/fehlgeschlagenes Foto,
    Claude liefert dann oft einen Platzhalter-Wert statt eines echten
    Fehlers) -- ohne diese Vorabprüfung könnte so ein Extremwert je nach
    Position in der Reihe (z.B. zwei gleich falsche Werte hintereinander am
    Ende, Praxisfall vom 17.06.2026) von der reinen Drei-Werte-Logik als
    "unklare Lage" übersehen werden, weil er gar keinen zweiten Extremwert
    als Vergleichspartner zur Bestätigung braucht.

    SCHRITT 1 -- iterativ, EIN Ausreißer pro Runde: Ein einzelner echter
    Ausreißer hat ZWEI direkte Nachbarn, die beide gegen ihn "falsch"
    aussehen (einer hat ihn als Nachfolger, einer als Vorgänger). Werden in
    einer Runde alle Kandidaten gleichzeitig entfernt, kann das dazu führen,
    dass zwei direkt benachbarte, eigenständige kleine Abweichungen sich
    gegenseitig fälschlich als Ausreißer "anstecken". Deshalb wird pro Runde
    nur der EINE Kandidat mit der größten Abweichung von seinen Nachbarn
    entfernt, danach wird die komplette Liste erneut von vorne geprüft.

    SCHRITT 2 -- Randprüfung: der allererste und allerletzte Wert der
    (bereinigten) Reihe wird separat geprüft (dafür gibt es ja keine zwei
    Nachbarn zum Vergleichen): ihr Sprung zum jeweils nächsten/vorherigen
    Wert darf nicht mehr als `edge_factor` mal so groß sein wie der
    TYPISCHE Sprung (Median der übrigen Differenzen)."""
    valid = [r for r in rows if r["_wert"] is not None]
    if len(valid) < 3:
        return set()

    outlier_filenames = set()

    # SCHRITT 0: absolute Sicherheitsregel über den Gesamtmedian aller Werte.
    all_values_sorted = sorted(r["_wert"] for r in valid)
    n_all = len(all_values_sorted)
    global_median = (
        all_values_sorted[n_all // 2] if n_all % 2 == 1
        else (all_values_sorted[n_all // 2 - 1] + all_values_sorted[n_all // 2]) / 2
    )
    for r in valid:
        # Median könnte theoretisch 0 sein (z.B. ganz am Anfang einer neuen
        # Zählerinstallation) -- dann greift die Regel bewusst nicht, um keine
        # Division-durch-Null-artige Überreaktion auf normale Werte zu erzeugen.
        if global_median > 0 and abs(r["_wert"] - global_median) > global_median * absolute_factor:
            outlier_filenames.add(r["dateiname"])

    remaining = [r for r in valid if r["dateiname"] not in outlier_filenames]

    # SCHRITT 1: iterative Ein-Kandidat-pro-Runde-Prüfung auf den verbleibenden Werten.
    max_rounds = len(remaining)  # Sicherheitsgrenze: nie mehr Runden als Werte
    for _ in range(max_rounds):
        if len(remaining) < 3:
            break

        candidates = []  # Liste von (abweichung, index, dateiname)
        for i in range(1, len(remaining) - 1):
            prev_val = remaining[i - 1]["_wert"]
            curr_val = remaining[i]["_wert"]
            next_val = remaining[i + 1]["_wert"]

            if prev_val <= curr_val <= next_val:
                continue  # passt in die Reihe, kein Kandidat

            if next_val >= prev_val:
                abweichung = max(curr_val - next_val, prev_val - curr_val, 0)
                candidates.append((abweichung, i, remaining[i]["dateiname"]))

        if not candidates:
            break

        candidates.sort(key=lambda c: c[0], reverse=True)
        _, idx, fname = candidates[0]
        outlier_filenames.add(fname)
        remaining.pop(idx)

    # SCHRITT 2: Randprüfung auf der finalen bereinigten Liste.
    diffs = [remaining[i + 1]["_wert"] - remaining[i]["_wert"] for i in range(len(remaining) - 1)]
    if diffs:
        diffs_sorted = sorted(diffs)
        n = len(diffs_sorted)
        typical_diff = diffs_sorted[n // 2] if n % 2 == 1 else (diffs_sorted[n // 2 - 1] + diffs_sorted[n // 2]) / 2
        threshold = max(typical_diff * edge_factor, 0.01)

        if len(remaining) >= 2:
            first_jump = remaining[1]["_wert"] - remaining[0]["_wert"]
            if abs(first_jump) > threshold:
                outlier_filenames.add(remaining[0]["dateiname"])

            last_jump = remaining[-1]["_wert"] - remaining[-2]["_wert"]
            if abs(last_jump) > threshold:
                outlier_filenames.add(remaining[-1]["dateiname"])

    return outlier_filenames


def main():
    rows, fieldnames = load_all_rows()
    print(f"{len(rows)} Zeilen insgesamt geladen.")

    rows, anzahl_entfernt, entfernte_details = remove_duplicate_timestamps(rows)
    if anzahl_entfernt > 0:
        print(f"\n{anzahl_entfernt} Zeile(n) mit doppeltem Zeitstempel entfernt "
              f"(vermutlich dasselbe Foto mehrfach verarbeitet):")
        for zeitstempel, dateiname, wert in entfernte_details:
            print(f"  {zeitstempel}: {dateiname} (wert={wert}) entfernt, bessere Lesbarkeit behalten")

    valid_count = sum(1 for r in rows if r["_wert"] is not None)
    print(f"\n{len(rows)} Zeilen nach Duplikat-Bereinigung, davon {valid_count} mit gültigem Zahlenwert.")

    if valid_count < 3:
        print("Zu wenige gültige Werte für eine Drei-Werte-Prüfung (mindestens 3 nötig).")
        if anzahl_entfernt == 0:
            return

    # Reihenfolge wichtig: zuerst find_rate_outliers, um grobe, in sich
    # konsistente Fehlserien zu entfernen (siehe Funktionsdoku, Praxisfall
    # 21.06.2026) -- DANACH erst find_outliers auf dem bereinigten Rest, da
    # eine unentdeckte Fehlserie sonst die feinere Drei-Werte-Prüfung
    # zusätzlich verwirren könnte (z.B. an den Übergängen in/aus der Serie).
    rate_outlier_filenames = find_rate_outliers(rows) if valid_count >= 5 else set()
    rows_ohne_rate_outliers = [r for r in rows if r["dateiname"] not in rate_outlier_filenames]
    outlier_filenames = find_outliers(rows_ohne_rate_outliers) if valid_count >= 3 else set()
    outlier_filenames |= rate_outlier_filenames

    if rate_outlier_filenames:
        print(f"\n{len(rate_outlier_filenames)} Zeile(n) wegen unplausibler Anstiegsrate "
              f"(>15x Median, vermutlich Lesefehler-Serie) markiert.")

    # Neu bewerten: jede Zeile mit gültigem Wert, die NICHT als Ausreißer
    # erkannt wurde, gilt jetzt als plausibel -- auch wenn sie vorher (durch
    # Fehlerfortpflanzung in read_meter.py) als 'nein' markiert war.
    changes = []
    for row in rows:
        if row["_wert"] is None:
            continue  # Fehlerzeilen ohne Wert bleiben unverändert

        was_plausibel = row["plausibel"]
        if row["dateiname"] in outlier_filenames:
            neu_plausibel = "nein"
            zusatz = "Nachbearbeitung: Wert passt nicht zwischen Vorgänger und Nachfolger (vermutlich Lesefehler)"
        else:
            neu_plausibel = "ja"
            zusatz = ""

        if was_plausibel != neu_plausibel:
            changes.append((row["dateiname"], was_plausibel, neu_plausibel))
            row["plausibel"] = neu_plausibel
            if zusatz:
                row["hinweis"] = (row["hinweis"] + " | " + zusatz) if row["hinweis"] else zusatz
            elif neu_plausibel == "ja":
                # War vorher fälschlich 'nein' wegen Fehlerfortpflanzung -- alten,
                # jetzt überholten Hinweis nicht stehen lassen.
                row["hinweis"] = ""

    if not changes and anzahl_entfernt == 0:
        print("Keine Änderungen nötig. CSV bleibt unverändert.")
        return

    if changes:
        print(f"\n{len(changes)} Zeile(n) zusätzlich neu bewertet:")
        for fname, alt, neu in changes:
            richtung = "wurde NEU als Ausreißer erkannt" if neu == "nein" else "war fälschlich unplausibel, jetzt korrigiert"
            print(f"  {fname}: {alt} -> {neu}  ({richtung})")

    backup_path = config.OUTPUT_CSV + ".bak"
    shutil.copy(config.OUTPUT_CSV, backup_path)
    print(f"\nOriginal-CSV gesichert als: {backup_path}")

    with open(config.OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})

    print(f"{config.OUTPUT_CSV} aktualisiert ({len(rows)} Zeilen, "
          f"{anzahl_entfernt} Duplikat(e) entfernt, {len(changes)} neu bewertet).")


if __name__ == "__main__":
    main()