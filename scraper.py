#!/usr/bin/env python3
"""Legge i lotti pubblici dalla piattaforma vendite Crono Aste.

Scrive due file:
  data/lotti.json     vendite attive (sostituito a ogni lettura)
  data/archivio.json  vendite concluse (si accumula: un lotto già archiviato non viene mai perso)

Viene eseguito da GitHub Actions (vedi .github/workflows/aggiorna.yml).
Se una lettura fallisce, il file corrispondente resta com'è.
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
SEARCH = BASE + "/ricerca.html?filter=stato%7C{stato}&page={page}"
# stato 1 = vendite attive; 2 = concluse con offerte; 3 = concluse senza offerte
STATI = {1: "attiva", 2: "conclusa", 3: "conclusa"}
ESITO = {2: "con offerte", 3: "senza offerte"}
DATA = Path(__file__).parent / "data"
MAX_PAGES = 60
HEADERS = {"User-Agent": "CronoAste-Vetrina/1.0 (+https://cronoaste.cloud)"}


def text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def parse_cards(html, stato):
    soup = BeautifulSoup(html, "html.parser")
    lotti = []
    for art in soup.select("article.auction"):
        link = art.select_one(".name_type a[href]")
        if not link:
            continue
        nome = art.select_one(".name_type")
        titolo = text(link.select_one("h3")) or link.get("title", "")

        # Tribunale e procedura: il testo cambia leggermente tra i due layout.
        m = re.search(r"Tribunale\s+(?:ordinario\s+)?di\s+(.+?)\s*[-–]?\s*Procedura\s*n\.?\s*(\S+)", text(nome), re.I)
        tribunale = m.group(1).strip(" -") if m else ""
        procedura = m.group(2).strip() if m else ""

        tipo_el = art.select_one(".tipo_asta") or nome.select_one("p[title]")
        prezzo_txt = text(art.select_one(".price-element"))
        try:
            prezzo = float(prezzo_txt.replace(".", "").replace(",", "."))
        except ValueError:
            prezzo = None
        img = art.select_one("img")
        visite = re.search(r"\d+", text(art.select_one(".n-vis")))
        lotti.append(
            {
                "id": art.get("data-auction-id", ""),
                "titolo": titolo,
                "tribunale": tribunale,
                "procedura": procedura,
                "tipo": text(tipo_el),
                "prezzo": prezzo,
                "etichetta": text(art.select_one(".price-block .label-desc")).rstrip(" :€").strip(),
                "inizio": art.get("data-auction-data-inizio", ""),
                "termine": art.get("data-auction-data-termine", ""),
                "stato": STATI[stato],
                "esito": ESITO.get(stato, ""),
                "foto": img.get("src", "") if img else "",
                "visite": int(visite.group()) if visite else 0,
                "url": link["href"],
            }
        )
    return lotti


def leggi(stato):
    """Scorre tutte le pagine di uno stato e restituisce i lotti trovati."""
    tutti, visti = [], set()
    for page in range(1, MAX_PAGES + 1):
        r = requests.get(SEARCH.format(stato=stato, page=page), headers=HEADERS, timeout=30)
        r.raise_for_status()
        nuovi = [l for l in parse_cards(r.text, stato) if l["id"] and l["id"] not in visti]
        if not nuovi:
            break
        visti.update(l["id"] for l in nuovi)
        tutti.extend(nuovi)
        time.sleep(1)  # gentile con il server
    return tutti


def carica(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
