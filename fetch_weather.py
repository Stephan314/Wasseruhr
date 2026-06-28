"""
Ruft historische Tageswetterdaten für Remscheid von der kostenlosen Open-Meteo
API ab (kein API-Key nötig) und speichert sie lokal in einer CSV-Datei
(config.WETTER_CSV) -- als Cache, damit analyze.py nicht bei jedem Lauf
erneut eine Internetverbindung braucht und die API nicht unnötig oft
angefragt wird.

Holt automatisch den Zeitraum ab, der in der bestehenden zaehlerstaende.csv
abgedeckt ist (plus einen Tag Puffer), und ergänzt nur fehlende Tage, falls
schon ein Cache existiert (idempotent wie die anderen Skripte).

Aufruf: python fetch_weather.py
"""
import csv
import os
from datetime import datetime, timedelta

import requests

import config


def get_needed_date_range() -> tuple:
    """Liest die Zeitstempel aus der bestehenden Zähler-CSV und gibt
    (erster_tag, letzter_tag) als date-Objekte zurück -- das ist der
    Zeitraum, für den wir Wetterdaten brauchen."""
    with open(config.OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        zeitstempel = []
        for row in reader:
            try:
                zeitstempel.append(datetime.fromisoformat(row["zeitstempel"]))
            except (ValueError, KeyError):
                continue

    if not zeitstempel:
        raise ValueError("Keine gültigen Zeitstempel in der Zähler-CSV gefunden.")

    return min(zeitstempel).date(), max(zeitstempel).date()


def load_existing_cache() -> dict:
    """Liest die bestehende Wetter-CSV ein (falls vorhanden), Rückgabe als
    {datum_iso: row_dict}, damit beim erneuten Aufruf nur fehlende Tage
    nachgeladen werden müssen."""
    if not os.path.exists(config.WETTER_CSV):
        return {}

    with open(config.WETTER_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return {row["datum"]: row for row in reader}


def fetch_weather_for_range(start_date, end_date) -> list:
    """Ruft Tageswetterdaten (Höchst-/Tiefsttemperatur, Niederschlag,
    Wettercode) für den angegebenen Zeitraum von der Open-Meteo Historical
    Weather API ab. Gibt eine Liste von dicts zurück, eine pro Tag."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": config.WETTER_LATITUDE,
        "longitude": config.WETTER_LONGITUDE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
        "timezone": "Europe/Berlin",
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    daily = data["daily"]
    zeilen = []
    for i, datum in enumerate(daily["time"]):
        zeilen.append({
            "datum": datum,
            "temp_max_c": daily["temperature_2m_max"][i],
            "temp_min_c": daily["temperature_2m_min"][i],
            "niederschlag_mm": daily["precipitation_sum"][i],
            "wettercode": daily["weather_code"][i],
        })
    return zeilen


def wettercode_zu_text(code) -> str:
    """Übersetzt den WMO-Wettercode in eine kurze, lesbare deutsche
    Beschreibung (nur die für Mitteleuropa relevanten Codes abgedeckt)."""
    if code is None or code == "":
        return ""
    code = int(float(code))
    mapping = {
        0: "Klar", 1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bedeckt",
        45: "Nebel", 48: "Reifnebel",
        51: "Leichter Sprühregen", 53: "Sprühregen", 55: "Starker Sprühregen",
        61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
        71: "Leichter Schneefall", 73: "Schneefall", 75: "Starker Schneefall",
        80: "Leichte Regenschauer", 81: "Regenschauer", 82: "Starke Regenschauer",
        95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Schweres Gewitter mit Hagel",
    }
    return mapping.get(code, f"Code {code}")


def main():
    start_date, end_date = get_needed_date_range()
    # Ein Tag Puffer auf beiden Seiten, falls die Zeitzone/Datumsgrenze knapp ist.
    start_date -= timedelta(days=1)
    end_date += timedelta(days=1)

    # WICHTIG: die Historical Weather API liefert nur abgeschlossene,
    # vergangene Tage (Reanalyse-Daten, die erst nach Tagesende final
    # berechnet werden können) -- nicht "heute" oder gar Zukunft. Außerdem
    # gibt es laut Open-Meteo-Dokumentation eine Verzögerung von ein paar
    # Tagen, bis ERA5-Daten final verfügbar sind. Sicherheitshalber wird
    # end_date daher auf "vor 3 Tagen" begrenzt; für die letzten paar Tage
    # bleibt die Wetterspalte in der Tagesübersicht dann leer, bis genug
    # Zeit vergangen ist.
    spaetest_verfuegbarer_tag = datetime.now().date() - timedelta(days=3)
    if end_date > spaetest_verfuegbarer_tag:
        end_date = spaetest_verfuegbarer_tag

    if start_date > end_date:
        print("Kein abrufbarer Zeitraum (alle Tage liegen zu nah an heute). "
              "Versuche es in ein paar Tagen erneut.")
        return

    print(f"Benötigter Zeitraum laut Zähler-CSV: {start_date} bis {end_date}")

    cache = load_existing_cache()
    fehlende_tage = []
    aktuelles_datum = start_date
    while aktuelles_datum <= end_date:
        if aktuelles_datum.isoformat() not in cache:
            fehlende_tage.append(aktuelles_datum)
        aktuelles_datum += timedelta(days=1)

    if not fehlende_tage:
        print(f"Alle Tage bereits im Cache ({config.WETTER_CSV}). Nichts zu tun.")
        return

    print(f"{len(fehlende_tage)} fehlende Tag(e), rufe Open-Meteo API ab...")
    neue_zeilen = fetch_weather_for_range(fehlende_tage[0], fehlende_tage[-1])

    for zeile in neue_zeilen:
        zeile["wetter_text"] = wettercode_zu_text(zeile["wettercode"])
        cache[zeile["datum"]] = zeile

    fieldnames = ["datum", "temp_max_c", "temp_min_c", "niederschlag_mm", "wettercode", "wetter_text"]
    os.makedirs(os.path.dirname(config.WETTER_CSV), exist_ok=True)
    with open(config.WETTER_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for datum in sorted(cache.keys()):
            row = cache[datum]
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    print(f"{config.WETTER_CSV} aktualisiert ({len(cache)} Tage insgesamt).")


if __name__ == "__main__":
    main()