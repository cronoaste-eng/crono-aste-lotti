#!/usr/bin/env python3
"""Legge i lotti pubblici dalla piattaforma vendite Crono Aste e scrive data/lotti.json.

Viene eseguito da GitHub Actions (vedi .github/workflows/aggiorna.yml).
Se la lettura fallisce, NON sovrascrive il file esistente: la vetrina continua
a mostrare l'ultimo elenco valido.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://cronoaste.fallcoaste.it"
SEARCH = BASE + "/ricerca.html?filter=stato%7C1&page={page}"  # stato 1 = vendite attive
OUT = Path(__file__).parent / "data" / "lotti.json"
MAX_PAGES = 40
HEADERS = {"User-Agent": "CronoAste-Vetrina/1.0 (+https://cronoaste.cloud)"}


def text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    lotti = []
    for art in soup.select("article.auction"):
        link = art.select_one(".name_type a[href]")
        if not link:
            continue
        titolo = text(link.select_one("h3")) or link.get("title", "")
        trib_raw = text(art.select_one(".trib-info"))
        m = re.match(r"(.*?)\s*Procedura\s*n\.?\s*(.+)", trib_raw, re.I)
        tribunale = (m.group(1) if m else trib_raw).strip()
        procedura = m.group(2).strip() if m else ""
        prezzo_txt = text(art.select_one(".price-element"))
        try:
            prezzo = float(prezzo_txt.replace(".", "").replace(",", "."))
        except ValueError:
            prezzo = None
        img = art.select_one("figure img")
        visite = re.search(r"\d+", text(art.select_one(".n-vis")))
        lotti.append(
            {
                "id": art.get("data-auction-id", ""),
                "titolo": titolo,
                "tribunale": tribunale.replace("Tribunale di ", "").strip(),
                "procedura": procedura,
                "tipo": text(art.select_one(".tipo_asta")),
                "prezzo": prezzo,
                "inizio": art.get("data-auction-data-inizio", ""),
                "termine": art.get("data-auction-data-termine", ""),
                "foto": img.get("src", "") if img else "",
                "visite": int(visite.group()) if visite else 0,
                "url": link["href"],
            }
        )
    return lotti


def main():
    tutti, visti = [], set()
    for page in range(1, MAX_PAGES + 1):
        r = requests.get(SEARCH.format(page=page), headers=HEADERS, timeout=30)
        r.raise_for_status()
        nuovi = [l for l in parse_cards(r.text) if l["id"] not in visti]
        if not nuovi:
            break
        for l in nuovi:
            visti.add(l["id"])
        tutti.extend(nuovi)
        time.sleep(1)  # gentile con il server

    if not tutti:
        print("Nessun lotto letto: lascio il file com'è.", file=sys.stderr)
        sys.exit(1)

    OUT.parent.mkdir(exist_ok=True)
    if OUT.exists():
        try:
            if json.loads(OUT.read_text(encoding="utf-8")).get("lotti") == tutti:
                print("Nessuna novità.")
                return
        except ValueError:
            pass
    OUT.write_text(
        json.dumps(
            {"aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"), "lotti": tutti},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Scritti {len(tutti)} lotti in {OUT}")


if __name__ == "__main__":
    main()
