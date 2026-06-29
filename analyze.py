"""
Auswertung der Wasserzähler-CSV: Verbrauchsberechnung, Visualisierung,
Erkennung von Unregelmäßigkeiten (nächtliche Grundlast als Leck-Indikator).

Voraussetzung: pandas, matplotlib installiert
    pip install pandas matplotlib

Aufruf: python analyze.py
"""
import os

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import config


def load_weather_cache() -> dict:
    """Liest die von fetch_weather.py erzeugte Wetter-CSV ein, falls
    vorhanden, und gibt {datum_iso: {...}} zurück. Robust gegen eine fehlende
    Datei (z.B. falls fetch_weather.py noch nie ausgeführt wurde) -- in dem
    Fall wird einfach ein leeres Dict zurückgegeben, die Tagesübersicht im
    Plot zeigt dann nur Verbrauch/Nullverbrauch ohne Wetterspalte, statt
    abzustürzen."""
    if not os.path.exists(config.WETTER_CSV):
        return {}
    wetter_df = pd.read_csv(config.WETTER_CSV)
    return {row["datum"]: row for _, row in wetter_df.iterrows()}


def load_data() -> pd.DataFrame:
    df = pd.read_csv(config.OUTPUT_CSV)

    gesamt_anzahl = len(df)
    # Fehlerhafte API-Aufrufe und als unplausibel markierte Werte (siehe read_meter.py,
    # check_plausibility) ausschließen, bevor überhaupt Deltas berechnet werden.
    df = df[(df["lesbarkeit"] != "FEHLER") & (df["plausibel"] == "ja")].copy()
    verworfen = gesamt_anzahl - len(df)
    if verworfen > 0:
        print(f"Hinweis: {verworfen} von {gesamt_anzahl} Zeilen wegen Fehler/Unplausibilität "
              f"ausgeschlossen (siehe Spalte 'hinweis' in der CSV für Details).")

    df["zeitstempel"] = pd.to_datetime(df["zeitstempel"])
    df["gesamtwert_m3"] = pd.to_numeric(df["gesamtwert_m3"], errors="coerce")
    df = df.dropna(subset=["gesamtwert_m3"])
    df = df.sort_values("zeitstempel").reset_index(drop=True)
    return df


def compute_consumption(df: pd.DataFrame) -> pd.DataFrame:
    """Berechnet Verbrauch zwischen aufeinanderfolgenden Messungen (Liter pro Intervall
    und Liter pro Minute, um auch bei Lücken in den Aufnahmen vergleichbar zu bleiben)."""
    df["delta_m3"] = df["gesamtwert_m3"].diff()
    df["delta_minuten"] = df["zeitstempel"].diff().dt.total_seconds() / 60
    df["liter_pro_minute"] = (df["delta_m3"] * 1000) / df["delta_minuten"]

    # Sicherheitsnetz: load_data() filtert bereits über die 'plausibel'-Spalte aus
    # read_meter.py, hier trotzdem nochmal auf negative Deltas prüfen, falls die CSV
    # zwischenzeitlich von Hand bearbeitet wurde oder ältere Einträge ohne diese
    # Spalte enthält.
    df.loc[df["delta_m3"] < 0, ["delta_m3", "liter_pro_minute"]] = None

    df["stunde"] = df["zeitstempel"].dt.hour
    df["ist_nacht"] = df["stunde"].between(1, 5)  # 01:00-05:59 als "Nacht" definiert

    # Größere zeitliche Lücken (z.B. Kameraspeicher voll) kennzeichnen: der
    # liter_pro_minute-Wert für so ein Intervall ist nur ein GROBER DURCHSCHNITT
    # über die gesamte Lücke und verschleiert echte Schwankungen darin (sowohl
    # Verbrauchsspitzen als auch echte Nullphasen). Der Wert selbst bleibt
    # unverändert (rechnerisch nicht falsch), wird aber separat markiert, damit
    # er im Plot als "keine Daten" statt als normaler Messpunkt kenntlich ist.
    df["ist_datenluecke"] = df["delta_minuten"] > config.DATENLUECKE_SCHWELLE_MINUTEN

    # Intervall gilt als "kein Wasser entnommen", wenn die Verbrauchsrate
    # unterhalb der konfigurierten Schwelle liegt. NaN-Werte (erste Zeile,
    # da kein Vorgänger existiert) zählen NICHT als Nullverbrauch. Eine
    # Datenlücke zählt ebenfalls NICHT als Nullverbrauch -- ein über 7 Stunden
    # gemittelter Wert unter der Schwelle bedeutet nicht, dass es 7 Stunden
    # durchgehend keinen Verbrauch gab, das wäre eine Scheingenauigkeit.
    df["ist_nullverbrauch"] = (
        df["liter_pro_minute"].notna()
        & (df["liter_pro_minute"] < config.ZERO_CONSUMPTION_THRESHOLD_L_MIN)
        & ~df["ist_datenluecke"]
    )
    return df


def detect_anomalies(df: pd.DataFrame) -> dict:
    """Einfache Heuristiken zur Anomalie-Erkennung:
    - Grundlast: gibt es nachts durchgehend einen Mindestverbrauch > 0?
    - Nächtliche Spitzen: ungewöhnlich hohe Einzelentnahmen in der Nachtzeit."""
    nacht_df = df[df["ist_nacht"]].dropna(subset=["liter_pro_minute"])
    tag_df = df[~df["ist_nacht"]].dropna(subset=["liter_pro_minute"])

    results = {}
    if not nacht_df.empty:
        results["nacht_median_l_min"] = nacht_df["liter_pro_minute"].median()
        results["nacht_min_l_min"] = nacht_df["liter_pro_minute"].min()
        results["nacht_max_l_min"] = nacht_df["liter_pro_minute"].max()
        # Grundlast-Verdacht: wenn auch das Minimum nachts deutlich über 0 liegt,
        # läuft vermutlich durchgehend Wasser irgendwo (z.B. Leck, tropfender Hahn).
        results["grundlast_verdacht"] = results["nacht_min_l_min"] > 0.05  # Schwellwert ggf. anpassen
    if not tag_df.empty:
        results["tag_median_l_min"] = tag_df["liter_pro_minute"].median()

    return results


def compute_zero_consumption_summary(df: pd.DataFrame) -> dict:
    """Berechnet pro Kalendertag die Gesamtdauer (in Minuten), in der kein
    Wasser entnommen wurde. Ein Intervall (Zeitspanne zwischen zwei
    aufeinanderfolgenden Messungen) zählt dabei vollständig zu dem Tag, an
    dem es ENDET (also dem Zeitstempel der jeweils späteren Messung) -- das
    ist eine bewusste Vereinfachung: bei 10-Minuten-Intervallen reicht kaum
    ein Intervall über Mitternacht, daher fällt diese Vereinfachung kaum auf,
    macht die Berechnung dafür aber deutlich einfacher als eine exakte
    Aufteilung anteilig auf zwei Kalendertage."""
    df = df.dropna(subset=["delta_minuten", "ist_nullverbrauch"]).copy()
    df["datum"] = df["zeitstempel"].dt.date

    nullverbrauch_minuten = (
        df[df["ist_nullverbrauch"]].groupby("datum")["delta_minuten"].sum()
    )
    return nullverbrauch_minuten.to_dict()


def compute_daily_consumption_summary(df: pd.DataFrame) -> dict:
    """Berechnet den Gesamtverbrauch (in m³) pro Kalendertag, mittels
    zeitanteiliger Verteilung jedes Intervalls (siehe
    _verteile_intervall_auf_stunden) statt einfacher Gruppierung nach
    Endzeitpunkt -- das verhindert, dass der Verbrauch einer größeren
    Datenlücke fälschlich komplett dem Tag zugerechnet wird, an dem die
    Lücke zufällig endet, statt anteilig auf die Tage verteilt zu werden,
    die die Lücke tatsächlich überspannt (z.B. eine Lücke von 22 Uhr bis
    6 Uhr am nächsten Tag).

    HINWEIS: für unvollständige Tage am Rand der Beobachtung (z.B. der
    allererste Tag, der erst ab 20:35 Uhr beginnt) zeigt dieser Wert nur den
    TATSÄCHLICH GEMESSENEN Anteil dieses Tages, nicht den hochgerechneten
    vollen Tagesverbrauch -- das ist beim Lesen der Werte zu beachten."""
    df = df.dropna(subset=["delta_m3"]).copy()
    df["start_zeit"] = df["zeitstempel"].shift(1)

    pro_tag = {}
    for _, row in df.iterrows():
        if pd.isna(row["start_zeit"]):
            continue
        anteile = _verteile_intervall_auf_stunden(row["start_zeit"], row["zeitstempel"], row["delta_m3"])
        for (datum, _stunde), wert in anteile.items():
            pro_tag[datum] = pro_tag.get(datum, 0) + wert

    return pro_tag


def _interpoliere_fuer_anzeige(df: pd.DataFrame) -> pd.DataFrame:
    """Erzeugt eine zur ANZEIGE im Liniendiagramm verwendete Kopie von df, bei
    der größere Datenlücken (ist_datenluecke) nicht mehr als EIN grob
    gemittelter Punkt erscheinen, sondern als mehrere stündliche
    Zwischenpunkte mit dem jeweils zeitanteiligen Verbrauch (siehe
    _verteile_intervall_auf_stunden) -- so wirkt die Linie während einer
    Lücke als sanfter Übergang statt als eine einzelne, künstlich flache
    Strecke. Rein kosmetisch: alle anderen Berechnungen (Hochrechnung,
    Gesamtverbrauch, Tagesprofil) bleiben unverändert, da sie nur auf den
    ORIGINALEN df-Werten beruhen, nicht auf dieser Anzeige-Kopie."""
    if not df["ist_datenluecke"].any():
        return df

    zusatz_zeilen = []
    df_sorted = df.sort_values("zeitstempel").reset_index(drop=True)

    for idx in range(1, len(df_sorted)):
        if not df_sorted.loc[idx, "ist_datenluecke"]:
            continue

        start = df_sorted.loc[idx - 1, "zeitstempel"]
        ende = df_sorted.loc[idx, "zeitstempel"]
        delta_m3 = df_sorted.loc[idx, "delta_m3"]
        start_wert = df_sorted.loc[idx - 1, "gesamtwert_m3"]

        anteile = _verteile_intervall_auf_stunden(start, ende, delta_m3)
        if len(anteile) <= 1:
            continue  # Lücke liegt innerhalb einer einzigen Stunde, nichts zu interpolieren

        # Anteile sind nach Stunde benannt, nicht zeitlich sortiert garantiert -- sortieren.
        sortierte_keys = sorted(anteile.keys())
        kumuliert = start_wert
        gesamt_minuten = (ende - start).total_seconds() / 60

        for i, key in enumerate(sortierte_keys[:-1]):  # letzter Punkt ist ja schon df_sorted.loc[idx]
            kumuliert += anteile[key]
            # Zeitpunkt: Ende der jeweiligen Stunde, die dieser Anteil repräsentiert.
            datum, stunde = key
            zwischenpunkt_zeit = pd.Timestamp.combine(datum, pd.Timestamp.min.time()) + pd.Timedelta(hours=stunde + 1)
            zwischenpunkt_zeit = min(zwischenpunkt_zeit, ende)

            neue_zeile = df_sorted.loc[idx].copy()
            neue_zeile["zeitstempel"] = zwischenpunkt_zeit
            neue_zeile["gesamtwert_m3"] = kumuliert
            # liter_pro_minute für die Zwischenpunkte: Rate dieses Teilstücks.
            segment_minuten = gesamt_minuten / len(sortierte_keys)  # grobe Näherung für die Anzeige
            neue_zeile["liter_pro_minute"] = (anteile[key] * 1000) / segment_minuten if segment_minuten > 0 else 0
            zusatz_zeilen.append(neue_zeile)

    if not zusatz_zeilen:
        return df

    df_mit_zwischenpunkten = pd.concat([df_sorted, pd.DataFrame(zusatz_zeilen)], ignore_index=True)
    return df_mit_zwischenpunkten.sort_values("zeitstempel").reset_index(drop=True)



def plot_consumption(df: pd.DataFrame, output_path: str = None):
    if output_path is None:
        output_path = config.OUTPUT_PLOT

    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    fig.suptitle(f"Wasserzähler Nr. {config.METER_SERIAL_NUMBER}", fontsize=11, color="gray")

    # Plot 1: Absoluter Zählerstand über Zeit
    axes[0].plot(df["zeitstempel"], df["gesamtwert_m3"], color="#1f77b4", linewidth=1,
                 label="Gemessener Zählerstand")

    # Lineare Regression über alle Messpunkte -> Trendgerade + Extrapolation.
    import numpy as np
    t0 = df["zeitstempel"].iloc[0]
    t_minuten = (df["zeitstempel"] - t0).dt.total_seconds() / 60
    koeffizienten = np.polyfit(t_minuten, df["gesamtwert_m3"], 1)
    steigung_m3_min = koeffizienten[0]
    achsenabschnitt = koeffizienten[1]

    t_extrapol = pd.date_range(start=df["zeitstempel"].iloc[0],
                               end=df["zeitstempel"].iloc[-1], periods=200)
    t_extrapol_min = (t_extrapol - t0).total_seconds() / 60
    y_extrapol = steigung_m3_min * t_extrapol_min + achsenabschnitt
    axes[0].plot(t_extrapol, y_extrapol, color="#ff7f0e", linewidth=1.2,
                 linestyle="--", alpha=0.7, label="Lineartrend (Regression)")
    axes[0].legend(loc="upper left", fontsize=8)

    # Kennzahlen für die Textbox berechnen.
    beobachtungs_stunden = (df["zeitstempel"].iloc[-1] - df["zeitstempel"].iloc[0]).total_seconds() / 3600
    beobachtungs_tage = beobachtungs_stunden / 24
    verbrauch_gesamt = df["gesamtwert_m3"].iloc[-1] - df["gesamtwert_m3"].iloc[0]
    verbrauch_pro_tag = verbrauch_gesamt / beobachtungs_tage
    hochrechnung_jahr = verbrauch_pro_tag * 365

    info_text = (
        f"Beobachtungszeitraum: {beobachtungs_tage:.1f} Tage\n"
        f"Gesamtverbrauch:  {verbrauch_gesamt:.2f} m³\n"
        f"Ø pro Tag:        {verbrauch_pro_tag:.2f} m³/Tag\n"
        f"Hochrechnung:  ≈ {hochrechnung_jahr:.0f} m³/Jahr"
    )
    axes[0].text(0.99, 0.05, info_text, transform=axes[0].transAxes,
                 fontsize=8, ha="right", va="bottom", family="monospace",
                 bbox=dict(boxstyle="round", facecolor="white", alpha=0.88, edgecolor="gray"))

    axes[0].set_ylabel("Zählerstand (m³)")
    axes[0].set_title("Wasserzähler: Absoluter Stand mit Lineartrend")
    axes[0].grid(alpha=0.3)

    # Plot 2: Verbrauchsrate (Liter/Minute)
    df_anzeige = _interpoliere_fuer_anzeige(df)
    axes[1].plot(df_anzeige["zeitstempel"], df_anzeige["liter_pro_minute"], color="#d62728",
                 linewidth=1, zorder=3, label="Verbrauchsrate")

    nacht_intervalle = _finde_zusammenhaengende_intervalle(df, "ist_nacht")
    for start, ende in nacht_intervalle:
        axes[1].axvspan(start, ende, color="navy", alpha=0.13, zorder=1)

    null_intervalle = _finde_zusammenhaengende_intervalle(df, "ist_nullverbrauch")
    for start, ende in null_intervalle:
        axes[1].axvspan(start, ende, color="#2ca02c", alpha=0.25, zorder=2)

    luecken_intervalle = _finde_zusammenhaengende_intervalle(df, "ist_datenluecke")
    for start, ende in luecken_intervalle:
        axes[1].axvspan(start, ende, facecolor="lightgray", edgecolor="gray",
                         hatch="///", alpha=0.5, zorder=4)

    # Tageszusammenfassung berechnen
    zusammenfassung = compute_zero_consumption_summary(df)
    tagesverbrauch = compute_daily_consumption_summary(df)
    wetter_cache = load_weather_cache()
    alle_tage = sorted(set(zusammenfassung.keys()) | set(tagesverbrauch.keys()))

    zeilen = []
    for tag in alle_tage:
        basis = (f"  {tag.strftime('%d.%m.')}: {tagesverbrauch.get(tag, 0):.2f} m³ | "
                 f"{int(zusammenfassung.get(tag, 0) // 60)}h {int(zusammenfassung.get(tag, 0) % 60)}min")
        wetter = wetter_cache.get(tag.isoformat())
        if wetter is not None:
            basis += f" | {wetter['temp_max_c']:.0f}°C"
            if wetter.get("niederschlag_mm", 0) and float(wetter["niederschlag_mm"]) > 0:
                basis += f" {wetter['niederschlag_mm']:.0f}mm"
        zeilen.append(basis)

    kopfzeile = "Pro Tag — Verbrauch | Kein Verbrauch" + (" | Wetter" if wetter_cache else "") + ":"
    legenden_text = kopfzeile + "\n" + "\n".join(zeilen)

    from matplotlib.patches import Patch
    legend_elemente = [
        plt.Line2D([0], [0], color="#d62728", linewidth=1, label="Verbrauchsrate"),
        Patch(facecolor="#2ca02c", alpha=0.25, label="Kein Verbrauch (< {:.1f} l/min)".format(
            config.ZERO_CONSUMPTION_THRESHOLD_L_MIN)),
        Patch(facecolor="navy", alpha=0.13, label="Nachtstunden (01-06 Uhr)"),
        Patch(facecolor="lightgray", edgecolor="gray", hatch="///", alpha=0.5,
              label=f"Datenlücke (> {config.DATENLUECKE_SCHWELLE_MINUTEN} Min., nur gemittelter Wert)"),
    ]
    axes[1].legend(handles=legend_elemente, loc="upper left", fontsize=8,
                    bbox_to_anchor=(0.0, 1.0))

    # Textblock mit Tageszusammenfassung
    axes[1].text(0.01, 0.74, legenden_text, transform=axes[1].transAxes,
                 fontsize=7.5, ha="left", va="top", family="monospace",
                 bbox=dict(boxstyle="round", facecolor="white", alpha=0.85, edgecolor="gray"))

    axes[1].set_ylabel("Verbrauch (Liter/Minute)")
    axes[1].set_title("Verbrauchsrate (grün = kein Verbrauch, blau = Nachtstunden)")
    axes[1].grid(alpha=0.3)
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    fig.autofmt_xdate()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"Plot gespeichert: {output_path}")


def _finde_zusammenhaengende_intervalle(df: pd.DataFrame, flag_spalte: str) -> list:
    """Hilfsfunktion: findet zusammenhängende Blöcke, in denen flag_spalte
    True ist, und gibt eine Liste von (start_zeit, end_zeit) Tupeln zurück --
    jeweils vom Zeitstempel des Intervall-STARTS (also dem vorherigen
    Messpunkt) bis zum Zeitstempel des aktuellen Messpunkts, damit das
    gezeichnete Band exakt die Zeitspanne abdeckt, für die der Wert gilt."""
    intervalle = []
    start = None
    vorheriger_zeitpunkt = None

    for _, row in df.iterrows():
        if row.get(flag_spalte, False):
            if start is None:
                start = vorheriger_zeitpunkt if vorheriger_zeitpunkt is not None else row["zeitstempel"]
            ende = row["zeitstempel"]
        else:
            if start is not None:
                intervalle.append((start, ende))
                start = None
        vorheriger_zeitpunkt = row["zeitstempel"]

    if start is not None:
        intervalle.append((start, ende))

    return intervalle


def _verteile_intervall_auf_stunden(start, ende, delta_m3: float) -> dict:
    """Teilt den Verbrauch eines einzelnen Intervalls (zwischen zwei
    Messpunkten) ANTEILIG nach Zeit auf alle (Datum, Stunde)-Buckets auf,
    die das Intervall überspannt."""
    gesamt_minuten = (ende - start).total_seconds() / 60
    if gesamt_minuten <= 0:
        return {}

    anteile = {}
    aktuell = start
    while aktuell < ende:
        stunden_ende = (aktuell.replace(minute=0, second=0, microsecond=0)
                         + pd.Timedelta(hours=1))
        segment_ende = min(stunden_ende, ende)
        segment_minuten = (segment_ende - aktuell).total_seconds() / 60

        key = (aktuell.date(), aktuell.hour)
        anteil_verbrauch = delta_m3 * (segment_minuten / gesamt_minuten)
        anteile[key] = anteile.get(key, 0) + anteil_verbrauch

        aktuell = segment_ende

    return anteile


def compute_hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Berechnet den typischen Verbrauch pro Tagesstunde (0-23 Uhr), gemittelt
    über alle Kalendertage, an denen diese Stunde tatsächlich Daten enthält."""
    df = df.dropna(subset=["delta_m3"]).copy()
    df["start_zeit"] = df["zeitstempel"].shift(1)

    pro_tag_und_stunde = {}
    for _, row in df.iterrows():
        if pd.isna(row["start_zeit"]):
            continue
        anteile = _verteile_intervall_auf_stunden(row["start_zeit"], row["zeitstempel"], row["delta_m3"])
        for key, wert in anteile.items():
            pro_tag_und_stunde[key] = pro_tag_und_stunde.get(key, 0) + wert

    pro_tag_und_stunde_series = pd.Series(pro_tag_und_stunde) * 1000  # m³ -> Liter
    if len(pro_tag_und_stunde_series) > 0:
        pro_tag_und_stunde_series.index = pd.MultiIndex.from_tuples(
            pro_tag_und_stunde_series.index, names=["datum", "stunde"]
        )

    profil = pro_tag_und_stunde_series.groupby("stunde").mean()
    anzahl_tage = pro_tag_und_stunde_series.groupby("stunde").size()

    result = pd.DataFrame({
        "verbrauch_liter_mittel": profil,
        "anzahl_tage": anzahl_tage,
    }).reindex(range(24))

    return result


def plot_hourly_profile(profil: pd.DataFrame, output_path: str = None):
    """Balkendiagramm: durchschnittlicher Verbrauch (Liter) je Tagesstunde."""
    if output_path is None:
        output_path = config.OUTPUT_HOURLY_PLOT

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.suptitle(f"Wasserzähler Nr. {config.METER_SERIAL_NUMBER}", fontsize=11, color="gray")

    stunden = profil.index
    werte = profil["verbrauch_liter_mittel"]

    bars = ax.bar(stunden, werte, color="#1f77b4", edgecolor="white", width=0.85)
    for h, bar in zip(stunden, bars):
        if 1 <= h <= 6:
            bar.set_color("#08306b")

    ax.set_xlabel("Stunde des Tages")
    ax.set_ylabel("Ø Verbrauch (Liter)")
    ax.set_title("Typisches Tagesprofil: durchschnittlicher Verbrauch pro Stunde "
                  "(Nachtstunden 01-06 Uhr dunkler)")
    ax.set_xticks(range(24))
    ax.grid(alpha=0.3, axis="y")

    for h in stunden:
        n = profil.loc[h, "anzahl_tage"]
        if pd.notna(n):
            ax.text(h, profil.loc[h, "verbrauch_liter_mittel"] + 0.3, f"n={int(n)}",
                    ha="center", fontsize=7, color="gray")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"Tagesprofil-Plot gespeichert: {output_path}")


def main():
    df = load_data()
    if df.empty:
        print("Keine gültigen Daten in der CSV gefunden.")
        return
 
    df = compute_consumption(df)
    anomalies = detect_anomalies(df)
 
    print(f"\nZeitraum: {df['zeitstempel'].min()} bis {df['zeitstempel'].max()}")
    print(f"Anzahl Messpunkte: {len(df)}")
    print(f"Gesamtverbrauch im Zeitraum: {df['gesamtwert_m3'].iloc[-1] - df['gesamtwert_m3'].iloc[0]:.3f} m³\n")
 
    print("--- Anomalie-Check ---")
    for key, val in anomalies.items():
        print(f"  {key}: {val}")
 
    if anomalies.get("grundlast_verdacht"):
        print("\n  ACHTUNG: Durchgehende nächtliche Grundlast erkannt -> mögliches Leck.")
    else:
        print("\n  Keine durchgehende nächtliche Grundlast erkannt -> kein klares Leck-Signal.")
 
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
 
    profil = compute_hourly_profile(df)
 
    # Archiv-Versionen mit Timestamp (z.B. plot_20260628_1435.png)
    plot_consumption(df, os.path.join(config.OUTPUT_DIR, f"plot_{timestamp}.png"))
    plot_hourly_profile(profil, os.path.join(config.OUTPUT_DIR, f"tagesprofil_{timestamp}.png"))
 
    # Aktuelle Versionen ohne Timestamp (werden bei jedem Lauf überschrieben)
    plot_consumption(df)
    plot_hourly_profile(profil)
 
 
if __name__ == "__main__":
    main()