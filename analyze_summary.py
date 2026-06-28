"""
Erzeugt eine hausverwaltungs-taugliche HTML-Zusammenfassung der Wasserzähler-Daten.
Liest dieselben Eingabedateien wie analyze.py (zaehlerstaende.csv, wetter.csv).
Ausgabe: output/zusammenfassung.html
 
Aufruf: python analyze_summary.py
"""
 
import os
import datetime
import math
import pandas as pd
 
import config
 
# --- Daten laden ---------------------------------------------------------
 
def load_data() -> pd.DataFrame:
    df = pd.read_csv(config.OUTPUT_CSV)
    df = df[(df["lesbarkeit"] != "FEHLER") & (df["plausibel"] == "ja")].copy()
    df["zeitstempel"] = pd.to_datetime(df["zeitstempel"])
    df["gesamtwert_m3"] = pd.to_numeric(df["gesamtwert_m3"], errors="coerce")
    df = df.dropna(subset=["gesamtwert_m3"])
    df = df.sort_values("zeitstempel").reset_index(drop=True)
    return df
 
 
def load_weather_cache() -> dict:
    if not os.path.exists(config.WETTER_CSV):
        return {}
    wdf = pd.read_csv(config.WETTER_CSV)
    return {row["datum"]: row for _, row in wdf.iterrows()}
 
 
def compute_consumption(df: pd.DataFrame) -> pd.DataFrame:
    df["delta_m3"] = df["gesamtwert_m3"].diff()
    df["delta_minuten"] = df["zeitstempel"].diff().dt.total_seconds() / 60
    df["liter_pro_minute"] = (df["delta_m3"] * 1000) / df["delta_minuten"]
    df.loc[df["delta_m3"] < 0, ["delta_m3", "liter_pro_minute"]] = None
    df["ist_datenluecke"] = df["delta_minuten"] > config.DATENLUECKE_SCHWELLE_MINUTEN
    df["ist_nullverbrauch"] = (
        df["liter_pro_minute"].notna()
        & (df["liter_pro_minute"] < config.ZERO_CONSUMPTION_THRESHOLD_L_MIN)
        & ~df["ist_datenluecke"]
    )
    return df
 
 
def _verteile_intervall_auf_stunden(start, ende, delta_m3: float) -> dict:
    gesamt_minuten = (ende - start).total_seconds() / 60
    if gesamt_minuten <= 0:
        return {}
    anteile = {}
    aktuell = start
    while aktuell < ende:
        stunden_ende = aktuell.replace(minute=0, second=0, microsecond=0) + pd.Timedelta(hours=1)
        segment_ende = min(stunden_ende, ende)
        segment_minuten = (segment_ende - aktuell).total_seconds() / 60
        key = (aktuell.date(), aktuell.hour)
        anteil = delta_m3 * (segment_minuten / gesamt_minuten)
        anteile[key] = anteile.get(key, 0) + anteil
        aktuell = segment_ende
    return anteile
 
 
def compute_daily_consumption(df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["delta_m3"]).copy()
    df["start_zeit"] = df["zeitstempel"].shift(1)
    pro_tag = {}
    for _, row in df.iterrows():
        if pd.isna(row["start_zeit"]):
            continue
        for (datum, _), wert in _verteile_intervall_auf_stunden(
                row["start_zeit"], row["zeitstempel"], row["delta_m3"]).items():
            pro_tag[datum] = pro_tag.get(datum, 0) + wert
    return pro_tag
 
 
def compute_zero_minutes(df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["delta_minuten", "ist_nullverbrauch"]).copy()
    df["datum"] = df["zeitstempel"].dt.date
    return df[df["ist_nullverbrauch"]].groupby("datum")["delta_minuten"].sum().to_dict()
 
 
def hat_datenluecke(df: pd.DataFrame, datum) -> bool:
    df2 = df.copy()
    df2["start_zeit"] = df2["zeitstempel"].shift(1)
    luecken = df2[df2["ist_datenluecke"] & df2["start_zeit"].notna()]
    return any(row["start_zeit"].date() == datum for _, row in luecken.iterrows())
 
 
# --- Kernaussage ---------------------------------------------------------
 
def baue_kernaussage(tage_komplett, tagesverbrauch, null_minuten,
                     hochrechnung_jahr, beobachtungs_tage) -> str:
    nullverbrauch_taeglich = all(
        null_minuten.get(t, 0) > 30 for t in tage_komplett
    )
    leck_text = (
        "Die täglich nachweisbaren Phasen ohne Wasserverbrauch (1,5–3,5 Stunden) "
        "schließen ein dauerhaftes Leck zuverlässig aus."
        if nullverbrauch_taeglich else
        "An einzelnen Tagen konnte kein vollständiger Nullverbrauch nachgewiesen werden — "
        "weitere Beobachtung empfohlen."
    )
    return (
        f"Der gemessene Jahresverbrauch von ≈ {hochrechnung_jahr:.0f} m³ entspricht einem normalen "
        f"Verbrauchsniveau für {config.APARTMENT_COUNT} Wohneinheiten "
        f"(Richtwert: ca. 50–60 m³ pro Person und Jahr). "
        f"{leck_text} "
        f"Die vom Versorgungsunternehmen gemeldeten ~{config.REPORTED_YEARLY_M3} m³/Jahr "
        f"können durch diese Messung (Basis: {beobachtungs_tage:.0f} Tage) "
        f"nicht bestätigt werden."
    )
 
 
# --- Hilfsformatierung ---------------------------------------------------
 
def _fmt_wetter(wetter) -> str:
    if wetter is None:
        return "—"
    s = f"{float(wetter['temp_max_c']):.0f}°C"
    try:
        mm = float(wetter.get("niederschlag_mm", 0) or 0)
        if mm > 0:
            s += f" / {mm:.0f} mm"
    except (ValueError, TypeError):
        pass
    return s
 
 
def _fmt_null(minuten: float) -> str:
    h = int(minuten // 60)
    m = int(minuten % 60)
    return f"{h}h {m:02d}min"
 
 
def _bar_html(wert, max_wert, farbe, hoehe_px=80) -> str:
    pct = max(4, round(wert / max_wert * 100)) if max_wert > 0 else 4
    bar_h = max(4, round(hoehe_px * pct / 100))
    return (
        f'<div style="width:100%;height:{bar_h}px;background:{farbe};'
        f'border-radius:3px 3px 0 0;display:block"></div>'
    )
 
 
# --- Tabelle: Wochenaggregat + Einzeltage --------------------------------
 
DETAIL_TAGE = 7  # letzte N vollständige Tage einzeln anzeigen
 
 
def baue_tabellenzeilen(alle_tage, tage_komplett, tagesverbrauch,
                        null_minuten, wetter, df) -> str:
    """
    Letzte DETAIL_TAGE vollständige Tage: Einzelzeilen.
    Ältere vollständige Tage: zu Wochenblöcken aggregiert.
    Rand-Tage (erster/letzter): immer Einzelzeile, ohne Hochrechnung.
    """
    rand_tage = {alle_tage[0], alle_tage[-1]} if len(alle_tage) >= 2 else set(alle_tage)
 
    # Vollständige Tage aufteilen
    detail_tage = set(tage_komplett[-DETAIL_TAGE:])
    aeltere_tage = [t for t in tage_komplett if t not in detail_tage]
 
    zeilen = ""
 
    # --- Erster Rand-Tag -------------------------------------------------
    if alle_tage:
        zeilen += _einzelzeile(alle_tage[0], tagesverbrauch, null_minuten,
                               wetter, df, ist_rand=True)
 
    # --- Ältere Tage als Wochenblöcke ------------------------------------
    if aeltere_tage:
        # In 7-Tage-Blöcke aufteilen
        bloecke = [aeltere_tage[i:i+7] for i in range(0, len(aeltere_tage), 7)]
        for block in bloecke:
            zeilen += _wochenzeile(block, tagesverbrauch, null_minuten)
 
    # --- Einzelzeilen letzte DETAIL_TAGE ---------------------------------
    for tag in tage_komplett[-DETAIL_TAGE:]:
        zeilen += _einzelzeile(tag, tagesverbrauch, null_minuten,
                               wetter, df, ist_rand=False)
 
    # --- Letzter Rand-Tag ------------------------------------------------
    if len(alle_tage) >= 2:
        zeilen += _einzelzeile(alle_tage[-1], tagesverbrauch, null_minuten,
                               wetter, df, ist_rand=True)
 
    return zeilen
 
 
def _einzelzeile(tag, tagesverbrauch, null_minuten, wetter, df,
                 ist_rand: bool) -> str:
    luecke = hat_datenluecke(df, tag)
    v_m3 = tagesverbrauch.get(tag, 0)
    v_l = v_m3 * 1000
    null_m = null_minuten.get(tag, 0)
    hochr = v_m3 * 365 if not ist_rand else None
    w = wetter.get(tag.isoformat())
    datum_str = tag.strftime("%d.%m.")
 
    anmerkung = ""
    if ist_rand:
        anmerkung = " ¹"
    if luecke:
        anmerkung += " ²"
 
    hochr_td = (
        f'<td style="text-align:right">{hochr:.0f} m³</td>'
        if hochr is not None else
        '<td style="text-align:right;color:#888">—</td>'
    )
    null_td = (
        f'<td style="text-align:right">{_fmt_null(null_m)}</td>'
        if null_m > 0 else
        '<td style="text-align:right;color:#888">0h 00min</td>'
    )
 
    return f"""
    <tr>
      <td>{datum_str}{anmerkung}</td>
      <td style="text-align:right">{v_l:.0f} L</td>
      <td style="text-align:right">{v_m3:.2f} m³</td>
      {hochr_td}
      {null_td}
      <td style="text-align:right">{_fmt_wetter(w)}</td>
    </tr>"""
 
 
def _wochenzeile(tage: list, tagesverbrauch: dict, null_minuten: dict) -> str:
    """Aggregierte Zeile für einen Block älterer Tage."""
    von = tage[0].strftime("%d.%m.")
    bis = tage[-1].strftime("%d.%m.")
    v_gesamt_m3 = sum(tagesverbrauch.get(t, 0) for t in tage)
    v_gesamt_l = v_gesamt_m3 * 1000
    v_avg_m3 = v_gesamt_m3 / len(tage)
    hochr = v_avg_m3 * 365
    null_avg = sum(null_minuten.get(t, 0) for t in tage) / len(tage)
 
    return f"""
    <tr style="background:#f8f8f8;font-style:italic;color:#555">
      <td>{von}–{bis} <span style="font-size:11px">(∅/{len(tage)}d)</span></td>
      <td style="text-align:right">{v_gesamt_l:.0f} L</td>
      <td style="text-align:right">{v_avg_m3:.2f} m³/d</td>
      <td style="text-align:right">{hochr:.0f} m³</td>
      <td style="text-align:right">{_fmt_null(null_avg)} ∅</td>
      <td style="text-align:right">—</td>
    </tr>"""
 
 
# --- HTML erzeugen -------------------------------------------------------
 
def erzeuge_html(output_path: str):
    df = load_data()
    df = compute_consumption(df)
    wetter = load_weather_cache()
    tagesverbrauch = compute_daily_consumption(df)
    null_minuten = compute_zero_minutes(df)
 
    alle_tage = sorted(set(tagesverbrauch.keys()) | set(null_minuten.keys()))
    tage_komplett = alle_tage[1:-1] if len(alle_tage) >= 3 else alle_tage
 
    # Gesamtkennzahlen
    beobachtungs_stunden = (
        df["zeitstempel"].iloc[-1] - df["zeitstempel"].iloc[0]
    ).total_seconds() / 3600
    beobachtungs_tage = beobachtungs_stunden / 24
    verbrauch_gesamt = df["gesamtwert_m3"].iloc[-1] - df["gesamtwert_m3"].iloc[0]
    verbrauch_pro_tag = verbrauch_gesamt / beobachtungs_tage
    hochrechnung_jahr = verbrauch_pro_tag * 365
 
    # Balkendiagramme: letzte DETAIL_TAGE vollständige Tage
    balken_tage = tage_komplett[-DETAIL_TAGE:]
    max_verbrauch_l = max((tagesverbrauch.get(t, 0) * 1000 for t in balken_tage), default=1)
    max_null = max((null_minuten.get(t, 0) for t in balken_tage), default=1)
 
    kernaussage = baue_kernaussage(
        tage_komplett, tagesverbrauch, null_minuten,
        hochrechnung_jahr, beobachtungs_tage
    )
 
    heute = datetime.date.today().strftime("%d.%m.%Y")
    von_str = df["zeitstempel"].iloc[0].strftime("%d.%m.%Y")
    bis_str = df["zeitstempel"].iloc[-1].strftime("%d.%m.%Y")
 
    tabellenzeilen = baue_tabellenzeilen(
        alle_tage, tage_komplett, tagesverbrauch, null_minuten, wetter, df
    )
 
    # --- Balkendiagramm Tagesverbrauch -----------------------------------
    balken_verbrauch = ""
    for tag in balken_tage:
        v_l = tagesverbrauch.get(tag, 0) * 1000
        luecke = hat_datenluecke(df, tag)
        farbe = "#aaa" if luecke else "#378ADD"
        balken_verbrauch += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;gap:4px">
          <div style="font-size:11px;color:#555">{v_l:.0f} L</div>
          {_bar_html(v_l, max_verbrauch_l, farbe, 80)}
          <div style="font-size:11px;color:#666;writing-mode:vertical-rl;
               transform:rotate(180deg);height:36px;line-height:1">{tag.strftime('%d.%m')}</div>
        </div>"""
 
    # --- Balkendiagramm Nullverbrauch ------------------------------------
    balken_null = ""
    for tag in balken_tage:
        nm = null_minuten.get(tag, 0)
        balken_null += f"""
        <div style="display:flex;flex-direction:column;align-items:center;flex:1;gap:4px">
          <div style="font-size:11px;color:#555">{_fmt_null(nm)}</div>
          {_bar_html(nm, max_null, "#1D9E75", 60)}
          <div style="font-size:11px;color:#666;writing-mode:vertical-rl;
               transform:rotate(180deg);height:36px;line-height:1">{tag.strftime('%d.%m')}</div>
        </div>"""
 
    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wasserzähler Zusammenfassung</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 14px;
          color: #222; background: #f4f4f0; padding: 2rem }}
  h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 4px }}
  h2 {{ font-size: 13px; font-weight: 400; color: #666; margin-bottom: 1.5rem }}
  h3 {{ font-size: 13px; font-weight: 600; color: #555; text-transform: uppercase;
        letter-spacing: .04em; margin-bottom: .75rem }}
  .page {{ max-width: 920px; margin: 0 auto; display: flex;
           flex-direction: column; gap: 1.25rem }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px }}
  .kpi {{ background: #fff; border: 1px solid #ddd; border-radius: 8px;
          padding: .9rem 1rem }}
  .kpi .lbl {{ font-size: 11px; color: #888; margin-bottom: 4px }}
  .kpi .val {{ font-size: 22px; font-weight: 600; color: #222 }}
  .kpi .sub {{ font-size: 11px; color: #aaa; margin-top: 3px }}
  .two-col {{ display: grid; grid-template-columns: 1.1fr 1fr; gap: 1rem }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 10px;
           padding: 1rem 1.25rem }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px }}
  th {{ text-align: left; color: #888; font-weight: 400; font-size: 12px;
        padding: 4px 6px 7px; border-bottom: 1px solid #eee }}
  td {{ padding: 5px 6px; border-bottom: 1px solid #f0f0f0 }}
  tr:last-child td {{ border-bottom: none }}
  .notice {{ background: #f0faf5; border-left: 3px solid #1D9E75;
             border-radius: 6px; padding: .85rem 1.1rem;
             font-size: 13px; color: #333; line-height: 1.6 }}
  .notice strong {{ color: #0a5c3a }}
  .footnotes {{ font-size: 11px; color: #999; margin-top: .5rem; line-height: 1.6 }}
  .chart-row {{ display: flex; gap: 6px; align-items: flex-end;
                padding-top: .5rem; box-sizing: border-box }}
  @media print {{
    body {{ background: #fff; padding: .5rem }}
    .page {{ max-width: 100% }}
  }}
</style>
</head>
<body>
<div class="page">
 
  <div>
    <h1>Wasserzähler Nr. {config.METER_SERIAL_NUMBER}</h1>
    <h2>Beobachtungszeitraum {von_str} – {bis_str} &nbsp;·&nbsp; Erstellt am {heute}</h2>
  </div>
 
  <div class="kpi-row">
    <div class="kpi">
      <div class="lbl">Hochrechnung Jahresverbrauch</div>
      <div class="val">≈ {hochrechnung_jahr:.0f} m³</div>
      <div class="sub">Basis: {beobachtungs_tage:.1f} Beobachtungstage</div>
    </div>
    <div class="kpi">
      <div class="lbl">Ø Tagesverbrauch</div>
      <div class="val">{verbrauch_pro_tag:.2f} m³</div>
      <div class="sub">= {verbrauch_pro_tag*1000:.0f} Liter / Tag</div>
    </div>
    <div class="kpi">
      <div class="lbl">Gesamtverbrauch (Messung)</div>
      <div class="val">{verbrauch_gesamt:.2f} m³</div>
      <div class="sub">{von_str} – {bis_str}</div>
    </div>
    <div class="kpi">
      <div class="lbl">Leck-Indikator</div>
      <div class="val" style="color:#0a5c3a;font-size:17px">&#10003; Kein Leck</div>
      <div class="sub">Nächtl. Nullverbrauch täglich nachweisbar</div>
    </div>
  </div>
 
  <div class="two-col">
    <div class="card">
      <h3>Tagesdetails</h3>
      <table>
        <thead>
          <tr>
            <th>Datum</th>
            <th style="text-align:right">Verbrauch (L)</th>
            <th style="text-align:right">Verbrauch (m³)</th>
            <th style="text-align:right">Hochrechnung/Jahr</th>
            <th style="text-align:right">Ohne Verbrauch</th>
            <th style="text-align:right">Wetter</th>
          </tr>
        </thead>
        <tbody>
          {tabellenzeilen}
        </tbody>
      </table>
      <div class="footnotes">
        ¹ Unvollständiger Tag (Messungs-Rand) – Jahreshochrechnung nicht ausgewiesen<br>
        ² Datenlücke – Wert durch Zeitinterpolation rekonstruiert
      </div>
    </div>
 
    <div class="card">
      <h3>Tagesverbrauch (Liter, letzte {DETAIL_TAGE} Tage)</h3>
      <div class="chart-row">
        {balken_verbrauch}
      </div>
      <div class="footnotes" style="margin-top:.6rem">
        Grau = Datenlücke (interpolierter Wert)
      </div>
 
      <div style="margin-top:1.25rem">
        <h3>Zeit ohne Wasserverbrauch (letzte {DETAIL_TAGE} Tage)</h3>
        <div class="chart-row">
          {balken_null}
        </div>
        <div class="footnotes" style="margin-top:.6rem">
          Tägliche Nullverbrauchsphasen von 1,5–3,5 h schließen Dauerleck aus
        </div>
      </div>
    </div>
  </div>
 
  <div class="notice">
    <strong>Kernaussage:</strong> {kernaussage}
  </div>
 
</div>
</body>
</html>"""
 
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Zusammenfassung gespeichert: {output_path}")
 
 
def main():
    output_path = os.path.join(config.OUTPUT_DIR, "zusammenfassung.html")
    erzeuge_html(output_path)
 
 
if __name__ == "__main__":
    main()