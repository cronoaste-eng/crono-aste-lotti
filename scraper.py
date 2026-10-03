#!/usr/bin/env python3
"""Legge i lotti pubblici dalla piattaforma vendite Crono Aste.

Scrive due file:
  data/lotti.json     vendite attive (sostituito a ogni lettura)
  data/archivio.json  vendite concluse (si accumula: un lotto già archiviato non viene mai perso)

Per ogni lotto legge anche la pagina del bene (una sola volta, poi il dato resta salvato) per
prendere "Prezzo minimo" e "Termine presentazione offerte" (con carta / con bonifico).

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
MAX_DETTAGLI = 400  # pagine dei beni lette al massimo in una esecuzione
HEADERS = {"User-Agent": "CronoAste-Vetrina/1.0 (+https://cronoaste.cloud)"}
CHIAVI_DETTAGLIO = ("prezzo_minimo", "termine_carta", "termine_bonifico", "pubblicata", "dett")
DATA_ORA = r"(\d{2}/\d{2}/\d{4})\s*(\d{1,2}:\d{2})"


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


def parse_dettagli(html):
    """Dalla pagina di un bene: prezzo minimo e termine di presentazione delle offerte."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    t = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    out = {"prezzo_minimo": None, "termine_carta": "", "termine_bonifico": "", "pubblicata": ""}

    m = re.search(r"Prezzo minimo\s*€?\s*:?\s*€?\s*(\d[\d.]*,\d{2})", t, re.I)
    if m:
        out["prezzo_minimo"] = float(m.group(1).replace(".", "").replace(",", "."))

    m = re.search(r"Data pubblicazione\s*:?\s*" + DATA_ORA, t, re.I)
    if m:
        out["pubblicata"] = f"{m.group(1)} {m.group(2)}"

    m = re.search(
        r"Termine presentazione offerte\s*:?(.*?)(?:Termine visita|Data vendita|Termine vendita|ID inserzione|$)",
        t,
        re.I,
    )
    if m:
        seg = m.group(1)
        c = re.search(r"con\s+Carta\s*:?\s*" + DATA_ORA, seg, re.I)
        b = re.search(r"con\s+Bonifico\s*:?\s*" + DATA_ORA, seg, re.I)
        if c:
            out["termine_carta"] = f"{c.group(1)} {c.group(2)}"
        if b:
            out["termine_bonifico"] = f"{b.group(1)} {b.group(2)}"
        if not c and not b:  # una sola data senza etichetta
            d = re.search(DATA_ORA, seg)
            if d:
                out["termine_carta"] = out["termine_bonifico"] = f"{d.group(1)} {d.group(2)}"
    return out


def arricchisci(lotti, cache, budget):
    """Aggiunge i dettagli ai lotti: li riusa se già letti, altrimenti apre la pagina del bene."""
    for l in lotti:
        vecchio = cache.get(l["id"])
        if vecchio and vecchio.get("dett") and "pubblicata" in vecchio:
            for k in CHIAVI_DETTAGLIO:
                if k in vecchio:
                    l[k] = vecchio[k]
            continue
        if budget[0] <= 0:
            continue
        budget[0] -= 1
        try:
            r = requests.get(l["url"], headers=HEADERS, timeout=30)
            r.raise_for_status()
            l.update(parse_dettagli(r.text))
            l["dett"] = True
        except Exception as e:  # noqa: BLE001
            print(f"Dettagli non letti per {l['id']}: {e}", file=sys.stderr)
        time.sleep(1)  # gentile con il server
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
        return json.loads(path.read_text(encoding="utf-8")).get("lotti", [])
    except (OSError, ValueError):
        return []


def pulisci(lotti):
    """Tiene solo link e foto https (difesa se i dati venissero alterati)."""
    for l in lotti:
        for k in ("url", "foto"):
            v = l.get(k) or ""
            if v and not re.match(r"^https://[^\s\"'<>]+$", v):
                l[k] = ""
    return lotti


def salva(path, lotti):
    lotti = pulisci(lotti)
    if carica(path) == lotti:
        print(f"{path.name}: nessuna novità.")
        return
    DATA.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {"aggiornato": datetime.now(timezone.utc).isoformat(timespec="seconds"), "lotti": lotti},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{path.name}: scritti {len(lotti)} lotti.")


def main():
    errori = 0
    budget = [MAX_DETTAGLI]
    # dettagli già letti in passato (lotti attivi + archivio), per non rileggere le stesse pagine
    cache = {l["id"]: l for l in carica(DATA / "archivio.json")}
    cache.update({l["id"]: l for l in carica(DATA / "lotti.json")})

    # 1) vendite attive: sostituiscono il file precedente
    try:
        attive = leggi(1)
        prec = len(carica(DATA / "lotti.json"))
        if attive and prec >= 10 and len(attive) < prec * 0.5:
            print(f"Lettura sospetta ({len(attive)} contro {prec}): lascio lotti.json com'è.", file=sys.stderr)
            errori += 1
        elif attive:
            salva(DATA / "lotti.json", arricchisci(attive, cache, budget))
        else:
            print("Nessun lotto attivo letto: lascio lotti.json com'è.", file=sys.stderr)
            errori += 1
    except Exception as e:  # noqa: BLE001
        print("Errore vendite attive:", e, file=sys.stderr)
        errori += 1

    # 2) vendite concluse: si accumulano nell'archivio
    try:
        concluse = leggi(2) + leggi(3)
        if concluse:
            concluse = arricchisci(concluse, cache, budget)
            archivio = {l["id"]: l for l in carica(DATA / "archivio.json")}
            for l in concluse:
                archivio[l["id"]] = l
            # dalla più recente alla più vecchia (data di termine gg/mm/aaaa hh:mm)
            def chiave(l):
                g = re.match(r"(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)", l.get("termine", ""))
                return tuple(int(x) for x in (g.group(3), g.group(2), g.group(1), g.group(4), g.group(5))) if g else (0,)
            salva(DATA / "archivio.json", sorted(archivio.values(), key=chiave, reverse=True))
        else:
            print("Nessuna vendita conclusa letta: lascio archivio.json com'è.", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print("Errore archivio:", e, file=sys.stderr)
        errori += 1

    sys.exit(1 if errori else 0)


if __name__ == "__main__":
    main()
