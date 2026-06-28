"""
3D-Visualisierung des Wasserverbrauchs: Tag (X-Achse) x Stunde des Tages
(Y-Achse) x Verbrauch in Litern (Z-Achse), als interaktive Plotly-HTML-Datei.

Macht sichtbar, ob sich Verbrauchsmuster (z.B. die nächtliche Nullphase)
zuverlässig JEDEN Tag zur gleichen Zeit wiederholen (regelmäßiges "Tal" über
alle Tage hinweg) oder unregelmäßig sind -- aussagekräftiger als das über
alle Tage gemittelte Tagesprofil-Balkendiagramm aus analyze.py, weil hier
jeder Tag einzeln sichtbar bleibt, statt verschmiert zu werden.

Verwendet dieselbe zeitanteilige Verteilung wie compute_hourly_profile() in
analyze.py: der Verbrauch eines Intervalls wird bei größeren Datenlücken
nicht der einen Endstunde zugerechnet, sondern proportional auf alle
überspannten Stunden verteilt (siehe analyze.py für Details/Praxisfall).

Erzeugt ZWEI Varianten, da je nach Anzahl der vorhandenen Tage die eine oder
andere besser ablesbar ist:
  - Surface-Plot: durchgehende, interpolierte Fläche (wirkt bei wenigen
    Tagen ggf. "klobig"/grob interpoliert, wird mit mehr Tagen glatter)
  - 3D-Balkendiagramm: einzelne Säulen pro (Tag, Stunde) -- bei wenigen
    Tagen meist klarer ablesbar, da keine Interpolation zwischen Tagen
    stattfindet, für die es gar keine Daten "dazwischen" gibt

Voraussetzung: pandas, plotly installiert
    pip install pandas plotly

Aufruf: python analyze_3d.py
"""
import pandas as pd
import plotly.graph_objects as go

import config
from analyze import load_data, compute_consumption, _verteile_intervall_auf_stunden


def compute_day_hour_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Berechnet den Verbrauch (Liter) pro (Tag, Stunde)-Kombination, OHNE
    über Tage zu mitteln (im Gegensatz zu compute_hourly_profile in
    analyze.py) -- jeder Tag bleibt als eigene Zeile erhalten, damit man
    Tag-zu-Tag-Unterschiede im 3D-Plot erkennen kann.

    Gibt ein DataFrame mit Spalten datum, stunde, verbrauch_liter zurück,
    inklusive (datum, stunde)-Kombinationen ohne Daten als NaN (wichtig
    für eine vollständige, lückenlose Matrix im Plot -- sonst entstehen
    Löcher an Tagesrändern, die Datenlücken vorgaukeln, wo gar keine sind)."""
    df = df.dropna(subset=["delta_m3"]).copy()
    df["start_zeit"] = df["zeitstempel"].shift(1)

    pro_tag_und_stunde = {}
    for _, row in df.iterrows():
        if pd.isna(row["start_zeit"]):
            continue
        anteile = _verteile_intervall_auf_stunden(row["start_zeit"], row["zeitstempel"], row["delta_m3"])
        for key, wert in anteile.items():
            pro_tag_und_stunde[key] = pro_tag_und_stunde.get(key, 0) + wert

    if not pro_tag_und_stunde:
        return pd.DataFrame(columns=["datum", "stunde", "verbrauch_liter"])

    alle_tage = sorted(set(datum for datum, _ in pro_tag_und_stunde.keys()))

    # Vollständiges Gitter aus allen vorhandenen Tagen x allen 24 Stunden
    # aufbauen, damit die Matrix für den 3D-Plot lückenlos ist.
    vollstaendige_zeilen = []
    for tag in alle_tage:
        for stunde in range(24):
            wert_m3 = pro_tag_und_stunde.get((tag, stunde))
            vollstaendige_zeilen.append({
                "datum": tag,
                "stunde": stunde,
                "verbrauch_liter": wert_m3 * 1000 if wert_m3 is not None else None,
            })

    return pd.DataFrame(vollstaendige_zeilen)


def plot_3d_surface(matrix_df: pd.DataFrame, output_path: str = None):
    """Interaktiver 3D-Surface-Plot: durchgehende Fläche über Tag x Stunde."""
    if output_path is None:
        output_path = config.OUTPUT_3D_SURFACE

    pivot = matrix_df.pivot(index="stunde", columns="datum", values="verbrauch_liter")
    tage_labels = [d.strftime("%d.%m.") for d in pivot.columns]

    fig = go.Figure(data=[go.Surface(
        z=pivot.values,
        x=tage_labels,
        y=pivot.index,
        colorscale="Blues",
        colorbar=dict(title="Liter"),
        connectgaps=True,  # NaN-Lücken interpolieren, damit die Fläche nicht reißt
    )])

    fig.update_layout(
        title=f"Wasserzähler Nr. {config.METER_SERIAL_NUMBER}: Verbrauch pro Tag und Stunde (3D-Fläche)",
        scene=dict(
            xaxis_title="Tag",
            yaxis_title="Stunde des Tages",
            zaxis_title="Verbrauch (Liter)",
        ),
        width=1000,
        height=750,
    )

    fig.write_html(output_path)
    print(f"3D-Surface-Plot gespeichert: {output_path}")


def plot_3d_bars(matrix_df: pd.DataFrame, output_path: str = None):
    """Interaktives 3D-Balkendiagramm: eine Säule pro (Tag, Stunde)-Kombination.
    Im Gegensatz zum Surface-Plot wird hier NICHT zwischen Tagen interpoliert
    -- jede Säule steht für sich, was bei wenigen vorhandenen Tagen oft
    klarer abzulesen ist."""
    if output_path is None:
        output_path = config.OUTPUT_3D_BARS

    df = matrix_df.dropna(subset=["verbrauch_liter"]).copy()
    alle_tage = sorted(df["datum"].unique())
    tag_zu_index = {tag: i for i, tag in enumerate(alle_tage)}
    tage_labels = [t.strftime("%d.%m.") for t in alle_tage]

    # Jede (Tag, Stunde)-Kombination als eigener 3D-Balken (Mesh3d-Quader).
    # Plotly hat kein eingebautes "3D-Bar"-Trace, daher werden die Balken aus
    # einzelnen Quadern (8 Eckpunkte je Balken) zusammengesetzt.
    fig = go.Figure()

    breite = 0.4  # halbe Balkenbreite in beiden Richtungen
    max_wert = df["verbrauch_liter"].max()

    for _, row in df.iterrows():
        x0 = tag_zu_index[row["datum"]] - breite
        x1 = tag_zu_index[row["datum"]] + breite
        y0 = row["stunde"] - breite
        y1 = row["stunde"] + breite
        z0 = 0
        z1 = row["verbrauch_liter"]

        # Farbintensität nach Höhe des Balkens (einfache, helle->dunkle Skala).
        intensitaet = z1 / max_wert if max_wert > 0 else 0
        farbe = f"rgb({int(30 + 100 * (1 - intensitaet))}, {int(70 + 100 * (1 - intensitaet))}, {int(150 + 80 * intensitaet)})"

        # Eckpunkte: 0-3 = Boden (x0,y0)->(x1,y0)->(x1,y1)->(x0,y1), 4-7 = Deckel
        # analog. Triangulierung rechnerisch verifiziert (jede der 6 Quader-
        # flächen wird durch genau 2 nicht überlappende Dreiecke abgedeckt).
        fig.add_trace(go.Mesh3d(
            x=[x0, x1, x1, x0, x0, x1, x1, x0],
            y=[y0, y0, y1, y1, y0, y0, y1, y1],
            z=[z0, z0, z0, z0, z1, z1, z1, z1],
            i=[0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3],
            j=[1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4],
            k=[2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7],
            color=farbe,
            opacity=1.0,
            hovertext=f"{row['datum'].strftime('%d.%m.')} {row['stunde']}:00 Uhr: {row['verbrauch_liter']:.0f} L",
            hoverinfo="text",
            showscale=False,
        ))

    fig.update_layout(
        title=f"Wasserzähler Nr. {config.METER_SERIAL_NUMBER}: Verbrauch pro Tag und Stunde (3D-Balken)",
        scene=dict(
            xaxis=dict(title="Tag", tickvals=list(range(len(alle_tage))), ticktext=tage_labels),
            yaxis_title="Stunde des Tages",
            zaxis_title="Verbrauch (Liter)",
        ),
        width=1000,
        height=750,
        showlegend=False,
    )

    fig.write_html(output_path)
    print(f"3D-Balken-Plot gespeichert: {output_path}")


def main():
    df = load_data()
    df = compute_consumption(df)

    matrix_df = compute_day_hour_matrix(df)
    anzahl_tage = matrix_df["datum"].nunique()
    print(f"{anzahl_tage} Kalendertag(e) in der Matrix.")

    if anzahl_tage < 2:
        print("Zu wenige Tage für eine aussagekräftige 3D-Darstellung (mindestens 2 empfohlen).")

    plot_3d_surface(matrix_df)
    plot_3d_bars(matrix_df)


if __name__ == "__main__":
    main()
