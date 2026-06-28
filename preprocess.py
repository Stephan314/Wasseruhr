"""
Bildvorverarbeitung für Wasserzähler-Fotos.

Ersetzt den ursprünglich geplanten GIMP-Schritt komplett durch Pillow/OpenCV:
- erwartet Fotos in Unterordnern pro Bildreihe (RAW_PHOTOS_DIR/reihe_xyz/*.JPG)
- bestimmt den Crop-Bereich EINMAL pro Reihe automatisch per Kreiserkennung
  (meter_locator.py), da sich die Kameraposition zwischen Bildreihen verschieben
  kann (Stativ nicht fixiert), innerhalb einer Reihe aber stabil bleibt
- liest EXIF-Zeitstempel aus (für Sortierung/CSV)
- skaliert auf Zielgröße für die Vision-API
- speichert als komprimiertes JPEG im PROCESSED_DIR (flach, alle Reihen zusammen,
  da der Zeitstempel im Dateinamen für eindeutige Sortierung sorgt)

Aufruf direkt: python preprocess.py
"""
import os
from datetime import datetime
from PIL import Image

import config
import meter_locator


def get_exif_timestamp(img: Image.Image, fallback_path: str) -> datetime:
    """Liest den Aufnahmezeitpunkt aus den EXIF-Daten.
    Fällt auf die Dateiänderungszeit zurück, falls EXIF fehlt oder kaputt ist."""
    try:
        exif = img.getexif()
        # Tag 36867 = DateTimeOriginal
        raw = exif.get(36867) or exif.get(306)  # 306 = DateTime als Fallback
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.fromtimestamp(os.path.getmtime(fallback_path))


def resize_image(img: Image.Image) -> Image.Image:
    """Skaliert so, dass die lange Kante TARGET_LONG_EDGE entspricht.
    Vergrößert nie, nur verkleinert (Lumix-Fotos sind immer hochauflösend)."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= config.TARGET_LONG_EDGE:
        return img
    scale = config.TARGET_LONG_EDGE / long_edge
    new_size = (int(w * scale), int(h * scale))
    return img.resize(new_size, Image.LANCZOS)


def get_crop_box_for_series(series_dir: str, first_image_path: str) -> tuple[int, int, int, int] | None:
    """Bestimmt die Crop-Box für eine ganze Bildreihe einmalig anhand des
    ersten Fotos und nutzt sie für alle weiteren Fotos derselben Reihe.
    Ergebnis wird in einer kleinen Cache-Datei im Reihen-Ordner abgelegt,
    damit ein erneuter Lauf (z.B. nach Abbruch) den Crop nicht neu berechnen muss."""
    cache_file = os.path.join(series_dir, ".crop_box.txt")
    preview_file = os.path.join(series_dir, "_crop_preview.jpg")

    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            content = f.read().strip()
        if content == "NONE":
            return None
        left, top, right, bottom = map(int, content.split(","))
        return (left, top, right, bottom)

    box = meter_locator.find_meter_crop_box(first_image_path)
    meter_locator.save_crop_preview(first_image_path, box, preview_file)

    with open(cache_file, "w") as f:
        f.write(",".join(map(str, box)) if box else "NONE")

    print(f"    -> Vorschau gespeichert: {preview_file}")
    print(f"    -> Falls die Crop-Box falsch aussieht: '{cache_file}' öffnen und mit")
    print(f"       eigenen Werten 'left,top,right,bottom' überschreiben, dann erneut starten.")

    return box


def process_single_image(src_path: str, crop_box) -> str | None:
    """Verarbeitet ein einzelnes Bild mit der für seine Reihe bestimmten Crop-Box
    und speichert es im PROCESSED_DIR. Dateiname enthält den Zeitstempel im
    Format YYYYMMDD_HHMMSS für einfache Sortierung. Gibt den Zielpfad zurück,
    oder None bei Fehler."""
    try:
        with Image.open(src_path) as img:
            img = img.convert("RGB")  # falls CMYK/RGBA o.ä.
            timestamp = get_exif_timestamp(img, src_path)

            if crop_box is not None:
                img = img.crop(crop_box)

            img = resize_image(img)

            out_name = timestamp.strftime("%Y%m%d_%H%M%S") + ".jpg"
            out_path = os.path.join(config.PROCESSED_DIR, out_name)

            # Falls Zeitstempel kollidiert (z.B. zwei Fotos selbe Minute durch Rundung),
            # Suffix anhängen statt zu überschreiben.
            counter = 1
            base_out_path = out_path
            while os.path.exists(out_path):
                stem, ext = os.path.splitext(base_out_path)
                out_path = f"{stem}_{counter}{ext}"
                counter += 1

            img.save(out_path, "JPEG", quality=config.JPEG_QUALITY, optimize=True)
            return out_path
    except Exception as e:
        with open(config.ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()} - FEHLER bei {src_path}: {e}\n")
        return None


def list_series_dirs() -> list:
    """Gibt alle Unterordner (Bildreihen) in RAW_PHOTOS_DIR zurück, sortiert."""
    if not os.path.isdir(config.RAW_PHOTOS_DIR):
        raise FileNotFoundError(
            f"Quellordner nicht gefunden: {config.RAW_PHOTOS_DIR}\n"
            "Bitte in config.py RAW_PHOTOS_DIR anpassen."
        )
    return sorted(
        os.path.join(config.RAW_PHOTOS_DIR, d)
        for d in os.listdir(config.RAW_PHOTOS_DIR)
        if os.path.isdir(os.path.join(config.RAW_PHOTOS_DIR, d))
    )


def process_series(series_dir: str) -> list:
    """Verarbeitet eine einzelne Bildreihe: bestimmt den Crop einmalig anhand
    des ersten (alphabetisch/zeitlich frühesten) Fotos, wendet ihn auf alle
    neuen Fotos der Reihe an. Gibt Liste der neu erzeugten Pfade zurück."""
    series_name = os.path.basename(series_dir)
    raw_files = sorted(
        f for f in os.listdir(series_dir)
        if os.path.splitext(f)[1].lower() in config.VALID_EXTENSIONS
    )

    if not raw_files:
        print(f"  [{series_name}] keine Fotos gefunden, übersprungen.")
        return []

    first_image_path = os.path.join(series_dir, raw_files[0])
    crop_box = get_crop_box_for_series(series_dir, first_image_path)

    if crop_box is None:
        if config.CROP_FALLBACK_TO_FULL_IMAGE:
            print(f"  [{series_name}] KEIN Zähler erkannt -- verwende volles Bild als Fallback.")
        else:
            print(f"  [{series_name}] KEIN Zähler erkannt und Fallback deaktiviert -- Reihe übersprungen.")
            return []
    else:
        l, t, r, b = crop_box
        print(f"  [{series_name}] Crop-Box erkannt: ({l},{t})-({r},{b}), Größe {r-l}x{b-t}")

    new_outputs = []
    skipped = 0
    for fname in raw_files:
        src_path = os.path.join(series_dir, fname)
        marker = src_path + ".done"
        if os.path.exists(marker):
            skipped += 1
            continue

        result = process_single_image(src_path, crop_box)
        if result:
            new_outputs.append(result)
            open(marker, "w").close()
        else:
            print(f"    FEHLER bei: {fname} (siehe {config.ERROR_LOG})")

    print(f"  [{series_name}] {len(new_outputs)} neu verarbeitet, {skipped} bereits vorhanden übersprungen.")
    return new_outputs


def process_all_new() -> list:
    """Verarbeitet alle Bildreihen in RAW_PHOTOS_DIR."""
    series_dirs = list_series_dirs()
    if not series_dirs:
        print(
            f"Keine Bildreihen-Unterordner in {config.RAW_PHOTOS_DIR} gefunden.\n"
            "Erwartete Struktur: raw_photos/reihe_<name>/*.JPG"
        )
        return []

    print(f"{len(series_dirs)} Bildreihe(n) gefunden.\n")
    all_new = []
    for series_dir in series_dirs:
        all_new.extend(process_series(series_dir))

    print(f"\nGesamt: {len(all_new)} neue Bilder verarbeitet.")
    return all_new


if __name__ == "__main__":
    process_all_new()