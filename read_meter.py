"""
Liest Zählerstände aus den verarbeiteten Fotos via Claude Vision API
und schreibt sie als Zeilen in eine CSV-Datei.

Voraussetzung: ANTHROPIC_API_KEY als Umgebungsvariable gesetzt
    (Windows, PowerShell):  $env:ANTHROPIC_API_KEY = "sk-ant-..."
    (Windows, dauerhaft):   setx ANTHROPIC_API_KEY "sk-ant-..."

Aufruf: python read_meter.py
Mit Limit zum Testen (nur die ersten N neuen Bilder verarbeiten):
    python read_meter.py --limit 5
"""
import os
import csv
import json
import base64
import re
import argparse
from datetime import datetime, timedelta, timezone

import anthropic

import config


PROMPT = """Das ist ein Foto eines Wasserzählers (Itron Aquadis, Großzähler für einen Wohnblock).
Das Rollenzählwerk hat folgenden Aufbau:
1. SCHWARZE Ziffernrollen (links/Hauptteil) = volle Kubikmeter (m³).
2. ROTE Ziffernrollen (rechts daneben) = die ersten drei Nachkommastellen des
   Kubikmeterwerts, also Liter (z.B. rot "224" bedeutet 0,224 m³ = 224 Liter).
3. Ein kleines Zeigerrad rechts vom Rollenzählwerk (Skala "x0,0001 m³") für eine
   vierte Nachkommastelle. Dieses Zeigerrad ist NICHT nötig für die Verbrauchsanalyse
   und kann grob geschätzt werden -- wichtig sind nur die Ziffernrollen.

Der Gesamtwert ergibt sich durch Aneinanderhängen: schwarz "08147" + rot "224"
ergibt 8147,224 m³ (NICHT addieren, sondern als Dezimalstellen anhängen).

Lies die Ziffernrollen ab und antworte AUSSCHLIESSLICH mit einem JSON-Objekt,
ohne weiteren Text, ohne Markdown-Codeblock, in folgendem Format:

{
  "ziffern_schwarz": "<volle m³ von den schwarzen Rollen, z.B. 8147>",
  "ziffern_rot": "<Liter-Nachkommastellen von den roten Rollen, immer 3-stellig, z.B. 224>",
  "gesamtwert_m3": "<kombinierter Wert als Dezimalzahl, z.B. 8147.224>",
  "lesbarkeit": "<gut|mittel|schlecht>",
  "anmerkung": "<kurze Anmerkung, falls etwas unklar war, sonst leer>"
}

Wichtig: Falls einzelne Ziffern zwischen zwei Rollenstellungen stehen (Übergang),
nimm die Ziffer, die gerade im Fenster sichtbar ist, auch wenn sie leicht versetzt
wirkt. Falls etwas nicht eindeutig lesbar ist, gib deine beste Schätzung und setze
"lesbarkeit" entsprechend. Antworte NUR mit dem JSON-Objekt."""


def encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def parse_timestamp_from_filename(fname: str) -> str:
    """Erwartet Format YYYYMMDD_HHMMSS.jpg (ggf. mit _N Suffix) und gibt ISO-String zurück."""
    stem = os.path.splitext(fname)[0]
    match = re.match(r"(\d{8})_(\d{6})", stem)
    if not match:
        return ""
    dt = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    return dt.isoformat()


def parse_datetime_from_filename(fname: str) -> datetime | None:
    """Wie parse_timestamp_from_filename, gibt aber ein datetime-Objekt zurück."""
    stem = os.path.splitext(fname)[0]
    match = re.match(r"(\d{8})_(\d{6})", stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def load_processed_filenames_from_csv() -> set:
    """Liest bereits in der CSV vorhandene Dateinamen, um doppelte API-Aufrufe zu
    vermeiden. Gibt Basisdateinamen OHNE _N-Suffix zurück, damit Dateien die durch
    preprocess.py mit unterschiedlichen Suffixen erzeugt wurden (z.B. einmal
    20260622_032242.jpg, einmal 20260622_032242_1.jpg) nicht doppelt verarbeitet
    werden -- der Suffix entsteht durch unterschiedliche Kamera-Benennung und ist
    für die Identifikation des Aufnahmezeitpunkts irrelevant."""
    if not os.path.exists(config.OUTPUT_CSV):
        return set()
    processed = set()
    with open(config.OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Basis-Timestamp ohne _N Suffix speichern: 20260622_032242_1.jpg -> 20260622_032242
            stem = os.path.splitext(row["dateiname"])[0]
            basis = re.sub(r"_\d{1,2}$", "", stem)  # _1, _2 etc. am Ende entfernen (nicht _080242)
            processed.add(basis)
    return processed


def dateiname_basis(fname: str) -> str:
    """Gibt den Basis-Timestamp eines Dateinamens ohne _N-Suffix zurück.
    20260622_032242_1.jpg -> 20260622_032242
    20260622_032242.jpg   -> 20260622_032242"""
    stem = os.path.splitext(fname)[0]
    return re.sub(r"_\d{1,2}$", "", stem)


def load_plausible_values_from_csv() -> dict[datetime, float]:
    """Liest alle plausiblen Zählerstände aus der CSV und gibt sie als
    {datetime -> wert} zurück. Wird für chronologische Plausibilitätsprüfung
    bei nachträglich verarbeiteten Bildern verwendet."""
    result = {}
    if not os.path.exists(config.OUTPUT_CSV):
        return result
    with open(config.OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("plausibel") != "ja":
                continue
            try:
                dt = datetime.fromisoformat(row["zeitstempel"])
                val = float(row["gesamtwert_m3"])
                result[dt] = val
            except (ValueError, KeyError):
                continue
    return result


def find_last_plausible_before(dt: datetime,
                                known_values: dict[datetime, float],
                                session_values: dict[datetime, float]) -> float | None:
    """Sucht den letzten plausiblen Zählerstand VOR dem gegebenen Zeitstempel.
    Berücksichtigt sowohl die bereits in der CSV gespeicherten Werte (known_values)
    als auch die in dieser Session neu hinzugefügten (session_values)."""
    combined = {**known_values, **session_values}
    earlier = {ts: v for ts, v in combined.items() if ts < dt}
    if not earlier:
        return None
    return earlier[max(earlier)]


def check_plausibility(neuer_wert: float, letzter_wert: float | None) -> tuple[str, str]:
    """Prüft ob der neue Wert physikalisch plausibel ist.

    Zwei Grenzen:
    - Nach unten: Rückgang bis MAX_RUECKGANG_M3 toleriert (Leseunschärfe).
      Groesserer Rueckgang = UNPLAUSIBEL.
    - Nach oben: Anstieg ueber MAX_ANSTIEG_M3 = UNPLAUSIBEL (Ausreisser nach oben,
      z.B. Ziffer falsch erkannt). Schuetzt alle Nachfolger vor falscher Referenz.
    """
    if letzter_wert is None:
        return "ja", ""

    diff = neuer_wert - letzter_wert  # positiv = Anstieg, negativ = Rueckgang

    if diff < -config.MAX_RUECKGANG_M3:
        return "nein", (
            f"Wert zu klein: {neuer_wert} m3 vs. Referenz {letzter_wert} m3, "
            f"Rueckgang {-diff:.3f} m3 (Toleranz: {config.MAX_RUECKGANG_M3} m3)"
        )

    if diff > config.MAX_ANSTIEG_M3:
        return "nein", (
            f"Wert zu gross: {neuer_wert} m3 vs. Referenz {letzter_wert} m3, "
            f"Anstieg +{diff:.3f} m3 (Maximum: {config.MAX_ANSTIEG_M3} m3) -- Ausreisser nach oben"
        )

    return "ja", ""


def main():
    parser = argparse.ArgumentParser(description="Liest Wasserzähler-Fotos via Claude Vision API aus.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Nur die ersten N neuen Bilder verarbeiten (zum Testen, z.B. --limit 5)."
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("FEHLER: Umgebungsvariable ANTHROPIC_API_KEY ist nicht gesetzt.")
        return

    client = anthropic.Anthropic(api_key=api_key)

    if not os.path.isdir(config.PROCESSED_DIR):
        print(f"Verzeichnis {config.PROCESSED_DIR} existiert nicht. Erst preprocess.py ausführen.")
        return

    # Altersgrenze: Bilder deren Timestamp älter als MAX_ALTER_BILDER_TAGE ist,
    # werden übersprungen. Verhindert dass ältere UNPLAUSIBEL-Bilder bei jedem
    # Lauf erneut versucht werden (insbesondere bei Suffix-Varianten durch
    # unterschiedliche Kamera-Benennung).
    jetzt = datetime.now()
    altersgrenze = jetzt - timedelta(days=config.MAX_ALTER_BILDER_TAGE)

    already_done = load_processed_filenames_from_csv()
    all_files = sorted(
        f for f in os.listdir(config.PROCESSED_DIR)
        if f.lower().endswith(".jpg")
    )

    uebersprungen_alt = 0
    todo_files = []
    for f in all_files:
        basis = dateiname_basis(f)
        if basis in already_done:
            continue  # bereits erfolgreich oder als unplausibel verarbeitet
        # Alterscheck: Timestamp aus Dateiname parsen
        dt = parse_datetime_from_filename(f)
        if dt is not None and dt < altersgrenze:
            uebersprungen_alt += 1
            continue  # zu alt, nicht mehr versuchen
        todo_files.append(f)

    if uebersprungen_alt > 0:
        print(f"Hinweis: {uebersprungen_alt} Bilder älter als {config.MAX_ALTER_BILDER_TAGE} Tage "
              f"übersprungen (zu alt für Nachverarbeitung).")

    if not todo_files:
        print("Keine neuen Bilder zur Verarbeitung. CSV ist aktuell.")
        return

    if args.limit is not None:
        todo_files = todo_files[:args.limit]
        print(f"--limit {args.limit} aktiv: nur die ersten {len(todo_files)} neuen Bilder werden verarbeitet.")

    print(f"{len(todo_files)} neue Bilder werden an die Claude API gesendet...")

    # Alle bereits bekannten plausiblen Werte aus der CSV laden (für chronologische
    # Plausibilitätsprüfung bei nachträglich verarbeiteten/lückenfüllenden Bildern)
    known_plausible = load_plausible_values_from_csv()
    # Neu in dieser Session hinzugefügte plausible Werte (damit aufeinanderfolgende
    # Bilder innerhalb eines Laufs ebenfalls korrekt geprüft werden)
    session_plausible: dict[datetime, float] = {}

    if known_plausible:
        last_known_dt = max(known_plausible)
        last_known_val = known_plausible[last_known_dt]
        print(f"  (Referenzwerte aus CSV geladen: {len(known_plausible)} plausible Einträge, "
              f"letzter: {last_known_val} m³ um {last_known_dt.strftime('%d.%m. %H:%M')})")

    file_exists = os.path.exists(config.OUTPUT_CSV)
    with open(config.OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        fieldnames = [
            "dateiname", "zeitstempel", "ziffern_schwarz", "ziffern_rot",
            "gesamtwert_m3", "lesbarkeit", "plausibel", "hinweis", "anmerkung",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for i, fname in enumerate(todo_files, 1):
            img_path = os.path.join(config.PROCESSED_DIR, fname)
            timestamp = parse_timestamp_from_filename(fname)
            print(f"  [{i}/{len(todo_files)}] {fname} ...", end=" ")
            try:
                result = call_claude_vision(client, img_path)
                neuer_wert = float(result.get("gesamtwert_m3", "nan"))

                # Chronologischen Vorgänger bestimmen (nicht letzten CSV-Eintrag)
                bild_dt = parse_datetime_from_filename(fname)
                letzter_wert = find_last_plausible_before(bild_dt, known_plausible, session_plausible) \
                    if bild_dt else None

                plausibel, hinweis = check_plausibility(neuer_wert, letzter_wert)

                writer.writerow({
                    "dateiname": fname,
                    "zeitstempel": timestamp,
                    "ziffern_schwarz": result.get("ziffern_schwarz", ""),
                    "ziffern_rot": result.get("ziffern_rot", ""),
                    "gesamtwert_m3": result.get("gesamtwert_m3", ""),
                    "lesbarkeit": result.get("lesbarkeit", ""),
                    "plausibel": plausibel,
                    "hinweis": hinweis,
                    "anmerkung": result.get("anmerkung", ""),
                })
                f.flush()

                status = "OK" if plausibel == "ja" else "UNPLAUSIBEL"
                ref_info = f" [Ref: {letzter_wert} m³]" if letzter_wert is not None else " [kein Vorgänger]"
                print(f"{status} -> {neuer_wert} m³ ({result.get('lesbarkeit', '?')}){ref_info}"
                      + (f" -- {hinweis}" if hinweis else ""))

                if plausibel == "ja" and bild_dt:
                    session_plausible[bild_dt] = neuer_wert

            except Exception as e:
                print(f"FEHLER: {e}")
                writer.writerow({
                    "dateiname": fname,
                    "zeitstempel": timestamp,
                    "ziffern_schwarz": "", "ziffern_rot": "",
                    "gesamtwert_m3": "", "lesbarkeit": "FEHLER",
                    "plausibel": "nein", "hinweis": "API-Fehler oder Parse-Fehler",
                    "anmerkung": str(e),
                })
                f.flush()

    print(f"\nFertig. Ergebnisse in {config.OUTPUT_CSV}")


def call_claude_vision(client: anthropic.Anthropic, image_path: str) -> dict:
    """Schickt ein Bild an die Claude Vision API und parst die JSON-Antwort."""
    img_b64 = encode_image(image_path)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```json\s*|\s*```$", "", text).strip()
    return json.loads(text)


if __name__ == "__main__":
    main()