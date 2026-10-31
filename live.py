#!/usr/bin/env python3
"""Monitoraggio in diretta: scrive data/live.json con i soli lotti con asta in corso adesso
e quelli chiusi negli ultimi 30 minuti (per catturare l'esito).

Per ogni lotto salva SOLO conteggi e miglior offerta: nessun nome, user_id, provenienza o importo
delle singole offerte. Legge solo le pagine dei lotti in corso, una alla volta, con pausa.

Viene eseguito da GitHub Actions (vedi .github/workflows/live.yml), solo 07:00-21:00 ora di Roma.
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROMA = ZoneInfo("Europe/Rome")
DATA = Path(__file__).parent / "data"
LIVE = DATA / "live.json"
HEADERS = {"User-Agent": "CronoAste-Vetrina/1.0 (+https://cronoaste.cloud)"}
FMT = "%d/%m/%Y %H:%M"
ORE = (7, 21)  # ora di Roma, estremi inclusi
MARGINE = timedelta(minutes=30)  # lotti chiusi da meno di 30 minuti
MAX_LOTTI = 250
PAUSA = 0.4  # secondi tra una pagina e l'altra
TIMEOUT_TOTALE = 240  # secondi: oltre, salva quanto letto e si ferma


def text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def quando(s):
    try:
        return datetime.strptime(s or "", FMT).replace(tzinfo=ROMA)
    except ValueError:
        return None


def carica(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def numero(s):
    try:
        return float(s.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def leggi_pagina(html):
    """Conteggi, miglior offerta, stato e termine dalla pagina del lotto."""
    soup = BeautifulSoup(html, "html.parser")
    out = {"offerte": None, "offerenti": None, "rilanci": None, "migliore": None}
    m = re.search(r"Offerte\s*:\s*makeObservable\(", html)
    if m:
        try:
            dati, _ = json.JSONDecoder().raw_decode(html, m.end())
            elenco = dati.get("offerte") or []
            out["offerte"] = len(elenco)
            out["offerenti"] = len({o.get("user_id") for o in elenco if o.get("user_id")})
            if dati.get("count_rilanci") is not None:
                out["rilanci"] = int(dati["count_rilanci"])
            out["migliore"] = float(dati["importo_offerta_massima"]) if elenco and dati.get("importo_offerta_massima") else None
        except (ValueError, TypeError, AttributeError):
            pass
    prezzo_el = soup.select_one("#main_auction_price_block .price-element") or soup.select_one(".price-block .price-element")
    out["prezzo"] = numero(text(prezzo_el))
    etich = soup.select_one("#label_price_block")
    out["etichetta"] = text(etich).rstrip(" :€").strip() if etich else ""
    if out["migliore"] is not None:  # miglior offerta attuale (solo l'importo massimo, mai le singole offerte)
        out["prezzo"] = out["migliore"]
        if out["etichetta"].lower().startswith("prezzo base"):
            out["etichetta"] = "Miglior offerta"
    h2 = soup.select_one("td.status-block h2.auction-state")
    if h2:
        righe = [text(h2)] + [text(e) for e in h2.find_next_siblings() if text(e)]
        out["stato_asta"] = " - ".join(r for r in righe if r) or None
    else:
        out["stato_asta"] = None
    avviso = None
    for box in soup.select("div.alert.alert-warning.text-center"):
        if re.match(r"Gara\b", text(box)):
            avviso = text(box)
            break
    out["avviso"] = avviso
    out["termine"] = None
    # Termine reale (con i prolungamenti): lo script della pagina; la cella "Termine vendita" e il file dei lotti sono solo ripieghi.
    df = re.search(r"data_fine_vendita\s*=\s*'(\d{2}/\d{2}/\d{4})\s+(\d{1,2}):(\d{2})'", html)
    if df:
        out["termine"] = f"{df.group(1)} {int(df.group(2)):02d}:{df.group(3)}"
    for th in ([] if df else soup.find_all("th")):
        if text(th).rstrip(": ") == "Termine vendita":
            d = re.search(r"(\d{2}/\d{2}/\d{4})\s*(\d{1,2}:\d{2})", text(th.find_next_sibling("td")))
            if d:
                out["termine"] = f"{d.group(1)} {int(d.group(2)[:-3]):02d}:{d.group(2)[-2:]}"
            break
    v = re.search(r'eye-visible\.svg[^>]*>\s*<p class="mb-0">Visite:\s*(\d+)', html)
    out["visite"] = int(v.group(1)) if v else None
    return out


def classifica(pag, ora):
    """stato (attiva/conclusa/sospesa) ed esito, dalle scritte della pagina e dai conteggi."""
    testo = " ".join(filter(None, [pag.get("avviso"), pag.get("stato_asta")]))
    if re.search(r"sospes", testo, re.I):
        stato = "sospesa"
    elif re.search(r"chiusa|annullat|cancellat|aggiudicat|revocat", testo, re.I):
        stato = "conclusa"
    else:
        stato = "attiva"
    esito = ""
    if stato == "conclusa" or (stato == "sospesa" and pag.get("offerte")):
        esito = "con offerte" if pag.get("offerte") else "senza offerte"
    return stato, esito


def candidati(ora, precedente):
    """Lotti da controllare: gia' iniziati e con termine non oltre 30 minuti fa (+ quelli gia' in live.json)."""
    voci = {}
    for nome in ("lotti.json", "archivio.json"):
        for l in carica(DATA / nome).get("lotti", []):
            ini, ter = quando(l.get("inizio")), quando(l.get("termine"))
            if ini and ini <= ora and (not ter or ter >= ora - MARGINE - timedelta(minutes=90)):
                voci[l["id"]] = l
    for l in precedente.get("lotti", []):
        voci.setdefault(l["id"], None)
    return voci


def main():
    ora = datetime.now(ROMA)
    if not (ORE[0] <= ora.hour <= ORE[1]) and "--sempre" not in sys.argv:
        print(f"Fuori dalle ore d'asta ({ora:%H:%M}): non aggiorno.")
        return 3  # il ciclo del workflow si ferma
    precedente = carica(LIVE)
    voci = candidati(ora, precedente)
    # url delle pagine: dai file dei lotti
    url = {}
    for nome in ("lotti.json", "archivio.json"):
        for l in carica(DATA / nome).get("lotti", []):
            url[l["id"]] = l.get("url")
    ids = [i for i in voci if url.get(i)][:MAX_LOTTI]
    vecchi = {l["id"]: l for l in precedente.get("lotti", [])}
    risultato, inizio = [], time.time()
    letti = errori = 0
    for i in ids:
        if time.time() - inizio > TIMEOUT_TOTALE:
            print("Tempo massimo raggiunto: salvo quanto letto.", file=sys.stderr)
            break
        try:
            r = requests.get(url[i], headers=HEADERS, timeout=30)
            r.raise_for_status()
            pag = leggi_pagina(r.text)
            letti += 1
        except Exception as e:  # noqa: BLE001
            errori += 1
            print(f"Pagina non letta per {i}: {e}", file=sys.stderr)
            if i in vecchi:
                risultato.append(vecchi[i])  # tengo il dato precedente
            time.sleep(PAUSA)
            continue
        time.sleep(PAUSA)
        stato, esito = classifica(pag, ora)
        l = voci.get(i) or {}
        termine = pag["termine"] or l.get("termine") or ""
        ter = quando(termine)
        if stato == "attiva":
            if pag.get("stato_asta") and re.search(r"non avviata", pag["stato_asta"], re.I):
                continue
            if ter and ter < ora - MARGINE:
                continue  # termine passato ma ancora "attiva": dato non affidabile, lo lascio fuori
        else:
            if not ter or ter < ora - MARGINE:
                continue
        risultato.append(
            {
                "id": i,
                "offerte": pag["offerte"],
                "rilanci": pag["rilanci"],
                "prezzo": pag["prezzo"] if pag["prezzo"] is not None else l.get("prezzo"),
                "etichetta": pag["etichetta"] or l.get("etichetta", ""),
                "termine": termine,
                "stato_asta": pag["stato_asta"],
                "stato": stato,
                "esito": esito,
            }
        )
    risultato.sort(key=lambda x: (quando(x["termine"]) or ora, x["id"]))
    if letti == 0 and ids:
        print("Nessuna pagina letta: lascio live.json com'e'.", file=sys.stderr)
        return 1
    nuovo = {"aggiornato": ora.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"), "lotti": risultato}
    # scrive solo se i dati sono cambiati o l'ultimo aggiornamento ha piu' di 10 minuti (meno salvataggi inutili)
    try:
        vecchio_agg = datetime.fromisoformat(precedente.get("aggiornato"))
    except (TypeError, ValueError):
        vecchio_agg = None
    if precedente.get("lotti") == risultato and vecchio_agg and (ora - vecchio_agg) < timedelta(minutes=10):
        print(f"Nessuna novita' ({len(risultato)} lotti, letti {letti}).")
        return 0
    DATA.mkdir(exist_ok=True)
    LIVE.write_text(json.dumps(nuovo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"live.json: {len(risultato)} lotti (pagine lette {letti}, errori {errori}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
