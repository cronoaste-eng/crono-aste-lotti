#!/usr/bin/env python3
"""Prepara per il canale WhatsApp i beni nuovi e li manda per email (una sola volta al mattino).

WhatsApp non permette a un programma di scrivere nel canale: l'email contiene un riquadro di testo
per ogni bene, da copiare e incollare nel canale (ogni messaggio mostra cosi' la sua foto).

Stato (beni gia' preparati): nella cache di Actions, cartella state_wa (NON nel repository).
Variabili: WA_MODE = send (default) | dry (stampa, non invia e non salva) | test (invia gli ultimi 5 beni,
senza salvare lo stato). WA_FORCE=1 ignora il controllo dell'orario e del "gia' inviato oggi".
Primo avvio senza stato: segna come visti i beni presenti e non invia nulla.
Sabato e domenica non invia nulla e non cambia lo stato: il lunedi' arrivano anche i beni nuovi del fine settimana.
"""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KEY = os.environ.get("BREVO_API_KEY", "")
SENDER = os.environ.get("SENDER_EMAIL") or "newsletter@cronoaste.it"
SENDER_NAME = os.environ.get("SENDER_NAME") or "Crono Aste"
TO = os.environ.get("NOTIFY_EMAIL") or "cronoaste@gmail.com"
MODE = (os.environ.get("WA_MODE") or "send").strip().lower()
FORCE = os.environ.get("WA_FORCE") == "1"
ORA_INVIO = 8            # ora italiana dell'invio
MAX_BENI = 10            # al massimo 10 beni per email
BASE = Path(__file__).parent
LOTTI = BASE / "data" / "lotti.json"
STATO = BASE / "state_wa" / "stato.json"
SITO = "https://cronoaste.cloud/beni-in-vendita"
PIATTAFORMA = "https://cronoaste.fallcoaste.it"


def carica(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def euro(n):
    p = f"{float(n):.2f}".split(".")
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ".", p[0]) + "," + p[1]


def quando(s):
    m = re.search(r"(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)", s or "")
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5))) if m else None


def scadenza(l):
    d = [quando(l.get("termine_carta")), quando(l.get("termine_bonifico")), quando(l.get("termine"))]
    d = [x for x in d if x]
    return min(d) if d else datetime(2100, 1, 1)


def messaggio(l):
    """Testo di un bene, pronto per WhatsApp (gli asterischi fanno il grassetto)."""
    min_ = l.get("prezzo_minimo")
    prezzo = min_ if min_ is not None else l.get("prezzo")
    etichetta = "Offerta minima" if min_ is not None else "Prezzo base"
    titolo = (l.get("titolo") or "").strip()
    if len(titolo) > 160:
        titolo = titolo[:157] + "..."
    testa = "TRIB. %s - PROC. %s" % ((l.get("tribunale") or "-").upper(), l.get("procedura") or "-")
    if l.get("lotto"):
        testa += " - " + l["lotto"].upper()
    righe = ["*Nuovo bene in vendita*", "*%s*" % testa, titolo]
    if prezzo is not None:
        righe.append("%s: € %s" % (etichetta, euro(prezzo)))
    dep = l.get("termine_carta") or l.get("termine_bonifico") or ""
    ini = (l.get("inizio") or "").split(" ")
    if dep:
        righe.append("Offerte entro %s" % dep)
    if ini[0]:
        righe.append("Asta il %s%s" % (ini[0], (" ore " + ini[1]) if len(ini) > 1 else ""))
    righe.append(l.get("url") or PIATTAFORMA)
    return "\n".join(righe)


def email(beni, saltati):
    n = len(beni)
    oggetto = "WhatsApp - %d nuov%s be%s da pubblicare" % (n, "o" if n == 1 else "i", "ne" if n == 1 else "ni")
    box = []
    testo = []
    for i, l in enumerate(beni, 1):
        m = messaggio(l)
        testo.append("--- Messaggio %d di %d ---\n%s\n" % (i, n, m))
        box.append(
            '<p style="margin:16px 0 4px;font-size:12px;color:#5f6368"><b>Messaggio %d di %d</b></p>'
            '<pre style="white-space:pre-wrap;word-break:break-word;background:#f7f9fb;border:1px solid #dde5ec;'
            'border-radius:8px;padding:12px;font-family:Arial,sans-serif;font-size:14px;line-height:1.5;margin:0">%s</pre>'
            % (i, n, html.escape(m))
        )
    nota = ""
    if saltati:
        nota = "Altri %d beni nuovi non sono in questa email: li trovi su %s" % (saltati, SITO)
    corpo = (
        '<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto">'
        '<p style="font-size:18px;font-weight:bold;color:#002d59;margin:0 0 8px">%s</p>'
        "<p style=\"margin:0 0 4px\">Un riquadro per ogni bene: copia il testo e incollalo nel canale WhatsApp, "
        "<b>un messaggio per volta</b> (cosi' ogni messaggio mostra la sua foto).</p>%s"
        '<p style="color:#5f6368;font-size:12px;margin:16px 0 0">%s</p></div>'
        % (html.escape(oggetto), "".join(box), html.escape(nota))
    )
    return oggetto, corpo, "\n".join(testo) + ("\n" + nota if nota else "")


def invia(oggetto, corpo, testo):
    dati = json.dumps(
        {
            "sender": {"email": SENDER, "name": SENDER_NAME},
            "to": [{"email": TO}],
            "subject": oggetto,
            "htmlContent": corpo,
            "textContent": testo,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=dati,
        method="POST",
        headers={"api-key": KEY, "accept": "application/json", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main():
    d = carica(LOTTI)
    lotti = [l for l in (d or {}).get("lotti", []) if l.get("stato") == "attiva" and l.get("id")]
    if not lotti:
        print("Nessun lotto attivo letto: non faccio nulla.")
        return 0
    adesso = datetime.now(ZoneInfo("Europe/Rome"))
    stato = carica(STATO)

    if MODE == "test":
        recenti = sorted(lotti, key=lambda l: quando(l.get("pubblicata")) or datetime(2000, 1, 1), reverse=True)[:5]
        oggetto, corpo, testo = email(recenti, 0)
        invia("[PROVA] " + oggetto, corpo, testo)
        print("Email di prova inviata con %d beni." % len(recenti))
        return 0

    if stato is None:
        print("Primo avvio: segno come visti %d beni, nessuna email." % len(lotti))
        if MODE != "dry":
            STATO.parent.mkdir(exist_ok=True)
            STATO.write_text(json.dumps({"visti": [l["id"] for l in lotti], "ultimo_invio": ""}), encoding="utf-8")
        return 0

    oggi = adesso.strftime("%Y-%m-%d")
    if not FORCE:
        if adesso.weekday() >= 5:
            print("Sabato o domenica: nessuna email (i beni nuovi arrivano lunedi').")
            return 0
        if adesso.hour != ORA_INVIO:
            print("Non sono le %d in Italia (sono le %s): non faccio nulla." % (ORA_INVIO, adesso.strftime("%H:%M")))
            return 0
        if stato.get("ultimo_invio") == oggi:
            print("Email di oggi gia' inviata.")
            return 0

    visti = set(stato.get("visti", []))
    nuovi = sorted([l for l in lotti if l["id"] not in visti], key=scadenza)
    attuali = [l["id"] for l in lotti]
    if not nuovi:
        print("Nessun bene nuovo: nessuna email.")
        nuovo_stato = {"visti": attuali, "ultimo_invio": oggi}
    else:
        scelti = nuovi[:MAX_BENI]
        oggetto, corpo, testo = email(scelti, len(nuovi) - len(scelti))
        if MODE == "dry":
            print(oggetto)
            print(testo)
            return 0
        invia(oggetto, corpo, testo)
        print("Email inviata con %d beni (%d non inclusi)." % (len(scelti), len(nuovi) - len(scelti)))
        nuovo_stato = {"visti": attuali, "ultimo_invio": oggi}
    if MODE != "dry":
        STATO.parent.mkdir(exist_ok=True)
        STATO.write_text(json.dumps(nuovo_stato), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if MODE != "dry" and not KEY and MODE in ("send", "test"):
        print("Manca BREVO_API_KEY")
        sys.exit(1)
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print("Errore Brevo %s: %s" % (e.code, e.read().decode()[:300]))
        sys.exit(1)
