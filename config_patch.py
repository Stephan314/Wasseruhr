# Diese zwei Zeilen in config.py ersetzen:
#
# ALT:
#   WETTER_LATITUDE  = 51.18
#   WETTER_LONGITUDE = 7.19
#
# NEU (am Anfang der Datei import os und dotenv ergänzen):

import os
from dotenv import load_dotenv

load_dotenv()  # liest .env aus dem Projektverzeichnis

WETTER_LATITUDE  = float(os.getenv("WETTER_LATITUDE",  "51.18"))
WETTER_LONGITUDE = float(os.getenv("WETTER_LONGITUDE", "7.19"))

# Die Fallback-Werte (51.18 / 7.19) greifen nur wenn .env fehlt,
# z.B. direkt nach einem git clone ohne .env-Setup.
