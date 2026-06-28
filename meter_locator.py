"""
Automatische Lokalisierung des Wasserzählers im Foto per klassischer
Bildverarbeitung (OpenCV Hough-Kreistransformation).

WICHTIGER HINWEIS aus der Praxis: Die automatische Erkennung ist NICHT in
jedem Lichtverhältnis zuverlässig. Bei manchen Fotos (z.B. wenn die Wand im
Hintergrund ähnlich hell ist wie der Zähler) wählt cv2.HoughCircles einen
zu großen oder falsch positionierten Kreis. Deshalb gibt es einen manuellen
Override-Mechanismus: nach der automatischen Erkennung wird ein Vorschaubild
(*_preview.jpg) im jeweiligen Bildreihen-Ordner gespeichert. Falls die
erkannte Box offensichtlich falsch liegt, kann die Datei .crop_box.txt im
selben Ordner von Hand mit eigenen Koordinaten überschrieben werden
(Format: "left,top,right,bottom" in Original-Pixelkoordinaten), siehe
preprocess.py / get_crop_box_for_series().

Wird einmal pro Bildreihe aufgerufen (nicht pro Einzelfoto), siehe preprocess.py.
"""
import cv2
import numpy as np

import config


def find_meter_crop_box(image_path: str) -> tuple[int, int, int, int] | None:
    """Sucht den Wasserzähler im Bild und gibt eine Crop-Box als
    (left, top, right, bottom) in Original-Pixelkoordinaten zurück.

    Gibt None zurück, wenn kein plausibler Kreis gefunden wurde
    (Aufrufer sollte dann auf "kein Crop" zurückfallen)."""
    img = cv2.imread(image_path)
    if img is None:
        return None
    h, w = img.shape[:2]

    # Für die Erkennung stark verkleinern (Performance) -- HoughCircles auf
    # voller Lumix-Auflösung (5472x3648) ist unnötig langsam.
    work_width = config.CIRCLE_DETECT_WIDTH
    scale = work_width / w
    small = cv2.resize(img, (work_width, int(h * scale)))
    sh, sw = small.shape[:2]

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)

    min_radius = int(sw * config.CIRCLE_MIN_RADIUS_FRACTION)
    max_radius = int(sw * config.CIRCLE_MAX_RADIUS_FRACTION)

    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=sw * 0.3,
        param1=50,
        param2=40,
        minRadius=min_radius,
        maxRadius=max_radius,
    )

    if circles is None:
        return None

    # Ersten (stärksten) Treffer nehmen. ACHTUNG: das ist nicht immer der
    # tatsächliche Zähler -- siehe Modul-Docstring zum manuellen Override.
    x, y, r = circles[0][0]
    x_full, y_full, r_full = x / scale, y / scale, r / scale

    box_r = r_full * config.CROP_MARGIN_FACTOR
    left = max(0, int(x_full - box_r))
    top = max(0, int(y_full - box_r))
    right = min(w, int(x_full + box_r))
    bottom = min(h, int(y_full + box_r))
    return (left, top, right, bottom)


def save_crop_preview(image_path: str, crop_box: tuple[int, int, int, int] | None, out_path: str):
    """Speichert ein Vorschaubild mit eingezeichneter Crop-Box (falls vorhanden)
    auf dem VERKLEINERTEN Originalbild, damit man schnell von Auge prüfen kann,
    ob die automatische Erkennung sinnvoll war -- ohne das große Originalfoto
    öffnen zu müssen. Bei crop_box=None wird ein Hinweistext ins Bild geschrieben."""
    img = cv2.imread(image_path)
    if img is None:
        return
    h, w = img.shape[:2]
    work_width = 800
    scale = work_width / w
    small = cv2.resize(img, (work_width, int(h * scale)))

    if crop_box is not None:
        left, top, right, bottom = crop_box
        cv2.rectangle(
            small,
            (int(left * scale), int(top * scale)),
            (int(right * scale), int(bottom * scale)),
            (0, 255, 0), 3,
        )
    else:
        cv2.putText(small, "KEIN KREIS ERKANNT", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

    cv2.imwrite(out_path, small)