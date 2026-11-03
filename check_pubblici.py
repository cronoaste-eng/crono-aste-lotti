#!/usr/bin/env python3
"""Test: nei JSON pubblici (data/ e Worker live) non devono comparire campi riservati
(offerenti, visite, email, telefoni, chiavi/token). Esce con errore se ne trova.
Solo ASCII."""
import glob
import json
import re
import sys
import urllib.request

VIETATE = re.compile(r"offerent|visit|e-?mail|telefon|phone|chiave|token|password|secret|cookie", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_PUBBLICI = ["https://live-cronoaste.cronoaste.workers.dev/live.json"]


def scorri(x, percorso, trovati):
    if isinstance(x, dict):
        for k, v in x.items():
            if VIETATE.search(str(k)):
                trovati.append("%s: chiave vietata '%s'" % (percorso, k))
            scorri(v, percorso, trovati)
    elif isinstance(x, list):
        for v in x:
            scorri(v, percorso, trovati)
    elif isinstance(x, str) and EMAIL.search(x):
        trovati.append("%s: indirizzo email nei valori" % percorso)


def main():
    trovati = []
    for f in sorted(glob.glob("data/**/*.json", recursive=True)):
        try:
            scorri(json.load(open(f, encoding="utf-8")), f, trovati)
        except Exception as e:  # noqa: BLE001
            trovati.append("%s: illeggibile (%s)" % (f, e))
    if "--rete" in sys.argv:
        for u in URL_PUBBLICI:
            try:
                scorri(json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 controllo-cronoaste"}), timeout=20)), u, trovati)
            except Exception as e:  # noqa: BLE001
                trovati.append("%s: non raggiungibile (%s)" % (u, e))
    for t in trovati:
        print("CAMPO RISERVATO: " + t, file=sys.stderr)
    if trovati:
        return 1
    print("Controllo campi riservati ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
