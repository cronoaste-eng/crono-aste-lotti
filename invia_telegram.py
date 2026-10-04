#!/usr/bin/env python3
"""Invia su Telegram i beni nuovi della vetrina (uno per messaggio, con foto).

Legge data/lotti.json e tiene l'elenco dei beni gia' inviati in data/inviati.json.
Non tocca scraper.py. Se mancano i codici segreti (TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID)
non fa nulla. Al primo avvio con i codici NON invia niente: segna come "gia' visti" i beni
che ci sono gia', cosi' il canale non viene riempito di colpo. Da li' in poi invia solo i nuovi.

Variabile facoltativa TELEGRAM_DRY_RUN=1: stampa i messaggi senza inviarli e senza salvare.
"""
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data"
LOTTI = DATA / "lotti.json"
INVIATI = DATA / "inviati.json"
MAX_PER_ESECUZIONE = 5   # al massimo 5 beni nuovi per ogni giro (ogni ora)
MAX_MEMORIA = 3000       # quanti id ricordare
PIATTAFORMA = "https://cronoaste.fallcoaste.it"


def carica(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def euro(n):
    if n is None:
        return ""
    p = f"{float(n):.2f}".split(".")
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ".", p[0]) + "," + p[1]


def data_ora(s):
    m = re.search(r"(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)", s or "")
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5))) if m else None


def deposito(l):
    c, b = l.get("termine_carta"), l.get("termine_bonifico")
    if c and b and c != b:
        return f"Offerte con carta entro: {c}\nOfferte con bonifico entro: {b}"
    d = c or b
    return f"Deposito offerte entro: {d}" if d else ""


def testo(l):
    e = html.escape
    min_ = l.get("prezzo_minimo")
    prezzo = min_ if min_ is not None else l.get("prezzo")
    etichetta = "Offerta minima" if min_ is not None else "Prezzo base"
    titolo = (l.get("titolo") or "").strip()
    if len(titolo) > 220:
        titolo = titolo[:217] + "..."
    righe = [
        "<b>Nuovo bene in vendita</b>",
        f"Trib. {e(l.get('tribunale') or '-')} - Proc. n. {e(l.get('procedura') or '-')}",
        e(titolo),
        "",
    ]
    if prezzo is not None:
        righe.append(f"<b>{etichetta}: EUR {euro(prezzo)}</b>")
    if l.get("inizio"):
        righe.append(f"Inizio asta: {e(l['inizio'])}")
    dep = deposito(l)
    if dep:
        righe.append(e(dep))
    url = l.get("url") or PIATTAFORMA
    righe.append(f'<a href="{e(url, quote=True)}">Vedi il bene e partecipa</a>')
    return "\n".join(righe)[:1000]


def invia(token, chat, l, dry):
    t = testo(l)
    if dry:
        print("----\n" + t + "\nFOTO:", l.get("foto"))
        return True
    base = f"https://api.telegram.org/bot{token}/"
    foto = l.get("foto") or ""
    if foto.startswith("https://"):
        r = requests.post(base + "sendPhoto", data={"chat_id": chat, "photo": foto, "caption": t, "parse_mode": "HTML"}, timeout=30)
        if r.ok:
            return True
        print(f"Foto non accettata per {l.get('id')} ({r.status_code}), provo senza foto.", file=sys.stderr)
    r = requests.post(base + "sendMessage", data={"chat_id": chat, "text": t, "parse_mode": "HTML", "disable_web_page_preview": "false"}, timeout=30)
    if not r.ok:
        print(f"Invio non riuscito per {l.get('id')}: {r.status_code} {r.text[:200]}", file=sys.stderr)
    return r.ok


def main():
    dry = os.environ.get("TELEGRAM_DRY_RUN") == "1"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not dry and not (token and chat):
        print("Telegram: codici non impostati, non faccio nulla.")
        return
    dati = carica(LOTTI)
    lotti = [l for l in (dati or {}).get("lotti", []) if l.get("id")]
    if not lotti:
        print("Telegram: nessun lotto letto, non faccio nulla.")
        return
    inviati = carica(INVIATI)
    if inviati is None:
        if dry:
            print("Telegram (prova): primo avvio, segnerei come gia' visti", len(lotti), "beni senza inviare nulla.")
            return
        INVIATI.write_text(json.dumps({"id": [l["id"] for l in lotti]}, indent=1), encoding="utf-8")
        print(f"Telegram: primo avvio, segnati {len(lotti)} beni come gia' visti. Nessun invio.")
        return
    visti = list(inviati.get("id", []))
    nuovi = [l for l in lotti if l["id"] not in set(visti)]
    nuovi.sort(key=lambda l: data_ora(l.get("pubblicata")) or datetime.min)
    print(f"Telegram: {len(nuovi)} beni nuovi.")
    mandati = 0
    for l in nuovi[:MAX_PER_ESECUZIONE]:
        try:
            ok = invia(token, chat, l, dry)
        except Exception as ex:  # noqa: BLE001
            print("Errore invio:", ex, file=sys.stderr)
            ok = False
        if not ok:
            break  # mi fermo, riprovo al prossimo giro senza perdere nulla
        visti.append(l["id"])
        mandati += 1
        if not dry:
            INVIATI.write_text(json.dumps({"id": visti[-MAX_MEMORIA:]}, indent=1), encoding="utf-8")
            time.sleep(3)
    print(f"Telegram: inviati {mandati}.")


if __name__ == "__main__":
    main()
