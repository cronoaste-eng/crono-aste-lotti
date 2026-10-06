"""Controlla la lista Brevo del bollettino e avvisa via email quando ci sono nuovi iscritti.
Lo stato (indirizzi gia noti) NON e nel repository (e pubblico): sta nella cache di Actions."""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

KEY = os.environ.get("BREVO_API_KEY", "")
LIST_ID = os.environ.get("BREVO_LIST_ID") or "3"
SENDER = os.environ.get("SENDER_EMAIL") or "newsletter@cronoaste.it"
SENDER_NAME = os.environ.get("SENDER_NAME") or "Crono Aste"
TO = os.environ.get("NOTIFY_EMAIL") or "cronoaste@gmail.com"
STATO = os.path.join("state", "iscritti.json")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        "https://api.brevo.com/v3" + path,
        data=data,
        method=method,
        headers={"api-key": KEY, "accept": "application/json", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read().decode()
        return json.loads(t) if t else {}


def iscritti():
    out = set()
    off = 0
    while True:
        d = api("GET", "/contacts/lists/%s/contacts?limit=500&offset=%d" % (LIST_ID, off))
        c = d.get("contacts", [])
        for x in c:
            if x.get("emailBlacklisted"):
                continue
            e = (x.get("email") or "").strip().lower()
            if e:
                out.add(e)
        if len(c) < 500:
            break
        off += 500
    return sorted(out)


def invia(nuovi, tutti):
    n = len(nuovi)
    oggetto = "Nuovo iscritto al bollettino: " + nuovi[0] if n == 1 else "%d nuovi iscritti al bollettino" % n
    righe = ["Nuovi iscritti confermati:"] + ["- " + e for e in nuovi]
    righe += ["", "Elenco completo (%d iscritti):" % len(tutti)] + ["- " + e for e in tutti]
    righe += ["", "Gestione: Brevo > Contatti > Liste > Bollettino settimanale."]
    testo = "\n".join(righe)
    html = "<p><b>Nuovi iscritti confermati:</b></p><ul>" + "".join("<li>%s</li>" % e for e in nuovi) + "</ul>"
    html += "<p><b>Elenco completo (%d iscritti):</b></p><ul>" % len(tutti) + "".join("<li>%s</li>" % e for e in tutti) + "</ul>"
    html += "<p>Gestione: Brevo &gt; Contatti &gt; Liste &gt; Bollettino settimanale.</p>"
    api("POST", "/smtp/email", {
        "sender": {"email": SENDER, "name": SENDER_NAME},
        "to": [{"email": TO}],
        "subject": oggetto,
        "textContent": testo,
        "htmlContent": html,
    })


def main():
    if not KEY:
        print("Manca BREVO_API_KEY")
        return 1
    tutti = iscritti()
    prima = None
    if os.path.exists(STATO):
        with open(STATO) as f:
            prima = set(json.load(f).get("email", []))
    if prima is None:
        print("Primo controllo: %d iscritti registrati, nessuna notifica." % len(tutti))
    else:
        nuovi = [e for e in tutti if e not in prima]
        if nuovi:
            invia(nuovi, tutti)
            print("Notifica inviata per %d nuovi iscritti." % len(nuovi))
        else:
            print("Nessun nuovo iscritto (%d totali)." % len(tutti))
    os.makedirs("state", exist_ok=True)
    with open(STATO, "w") as f:
        json.dump({"email": tutti, "aggiornato": datetime.utcnow().isoformat()}, f)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print("Errore Brevo %s: %s" % (e.code, e.read().decode()[:300]))
        sys.exit(1)
