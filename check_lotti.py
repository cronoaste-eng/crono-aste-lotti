#!/usr/bin/env python3
"""Controllo dei dati dopo lo scraper e PRIMA del commit/Telegram.
Se data/lotti.json e' vuoto, illeggibile, con troppi beni senza campi base o con meno del 35% dei beni
della versione gia' pubblicata, ripristina i file dall'ultimo commit e termina con errore (esecuzione rossa).
Solo ASCII."""
import json
import subprocess
import sys

FILE = "data/lotti.json"
SOGLIA = 0.35
MIN_VECCHI = 15


def vecchio():
    try:
        out = subprocess.run(["git", "show", "HEAD:" + FILE], capture_output=True, text=True, check=True).stdout
        return len(json.loads(out).get("lotti", []))
    except Exception:  # noqa: BLE001
        return None


def ripristina(motivo):
    print("CONTROLLO DATI FALLITO: %s. Ripristino i dati dall'ultimo commit." % motivo, file=sys.stderr)
    subprocess.run(["git", "checkout", "--", "data/lotti.json", "data/archivio.json", "data/inviati.json"], check=False)
    return 1


def main():
    try:
        lotti = json.load(open(FILE, encoding="utf-8")).get("lotti")
    except Exception as e:  # noqa: BLE001
        return ripristina("lotti.json illeggibile (%s)" % e)
    if not isinstance(lotti, list) or not lotti:
        return ripristina("nessun lotto")
    senza = [l for l in lotti if not (isinstance(l, dict) and l.get("id") and (l.get("titolo") or l.get("url")))]
    if len(senza) > 0.25 * len(lotti):
        return ripristina("%d lotti su %d senza id/titolo/url" % (len(senza), len(lotti)))
    prima = vecchio()
    if prima and prima >= MIN_VECCHI and len(lotti) < SOGLIA * prima:
        return ripristina("lotti scesi da %d a %d" % (prima, len(lotti)))
    print("Controllo dati ok: %d lotti (prima %s)." % (len(lotti), prima))
    return 0


if __name__ == "__main__":
    sys.exit(main())
