# -*- coding: utf-8 -*-
"""Controllo notturno di tutto il sistema Crono Aste.

Controlla il giorno appena finito (ora italiana) e manda un report con
OK / ATTENZIONE / ERRORE per ogni voce. Un run "success" che non ha fatto
il suo lavoro conta come ERRORE: si guarda l'esito reale (file, invii, conteggi).

Modalita' (CONTROLLO_MODE): prova = stampa il report e non invia nulla;
send = manda il report SOLO alla chat Telegram privata (TELEGRAM_CHAT_ID_PRIVATA,
mai al canale pubblico); se Telegram non risponde prova con un'email via Brevo.

Variabili: GH_TOKEN, CONTROLLO_MODE, CONTROLLO_GIORNO (AAAA-MM-GG, facoltativo),
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID_PRIVATA, BREVO_API_KEY, NOTIFY_EMAIL,
SENDER_EMAIL, CHIAVE_PROMEMORIA (per /api/stato del sito), GITHUB_RUN_ID.
Solo libreria standard. Nessun segreto viene stampato.
"""
import datetime as dt
import io
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")
UTC = dt.timezone.utc
REPO = "cronoaste-eng/crono-aste-lotti"
REPO_SITO = "cronoaste-eng/sito-cronoaste"
SITO = "https://cronoaste.cloud"
WORKER = "https://live-cronoaste.cronoaste.workers.dev"
MODE = os.environ.get("CONTROLLO_MODE", "prova").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "")
UA = "cronoaste-controllo"

# Scadenze note (nessun segreto: solo date). Avviso a 30 e 7 giorni.
SCADENZE = [
    ("Token Pagina Meta (accesso dati)", dt.date(2027, 1, 23)),
    ("Token GitHub avvio-workflows-live", dt.date(2027, 2, 18)),
    ("Dominio cronoaste.cloud", dt.date(2027, 10, 11)),
]

OK, ATT, ERR, NV = "OK", "ATTENZIONE", "ERRORE", "NON VERIFICATO"
ICONA = {OK: "✅", ATT: "⚠️", ERR: "❌", NV: "❔"}
risultati = []  # (voce, esito, motivo)


def esito(voce, stato, motivo):
    risultati.append((voce, stato, motivo))


# ---------------------------------------------------------------- rete
def richiesta(url, headers=None, dati=None, metodo=None, timeout=30, grezzo=False):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=dati, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            corpo = r.read()
            return r.status, dict(r.headers), corpo if grezzo else corpo.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        corpo = e.read()
        return e.code, dict(e.headers), corpo if grezzo else corpo.decode("utf-8", "replace")


def gh(percorso, grezzo=False):
    url = percorso if percorso.startswith("http") else "https://api.github.com/" + percorso.lstrip("/")
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GH_TOKEN:
        h["Authorization"] = "Bearer " + GH_TOKEN
    st, _, corpo = richiesta(url, h, grezzo=grezzo)
    if st != 200:
        raise RuntimeError("GitHub %s su %s" % (st, percorso.split("?")[0]))
    return corpo if grezzo else json.loads(corpo)


def iso(t):
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))


def ora_roma(t):
    return t.astimezone(ROMA).strftime("%H:%M")


# ---------------------------------------------------------------- giorno
def giorno_da_controllare():
    g = os.environ.get("CONTROLLO_GIORNO", "").strip()
    if g:
        return dt.date.fromisoformat(g)
    return (dt.datetime.now(ROMA) - dt.timedelta(days=1)).date()


G = giorno_da_controllare()
INIZIO = dt.datetime.combine(G, dt.time(0, 0), ROMA).astimezone(UTC)
FINE = dt.datetime.combine(G + dt.timedelta(days=1), dt.time(0, 0), ROMA).astimezone(UTC)
FERIALE = G.weekday() < 5
GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]


def z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_del_giorno(workflow, repo=REPO):
    """Tutti i run del workflow creati nel giorno G (ora italiana)."""
    tutti, pagina = [], 1
    filtro = urllib.parse.quote("%s..%s" % (z(INIZIO), z(FINE)))
    while True:
        d = gh("repos/%s/actions/workflows/%s/runs?per_page=100&page=%d&created=%s" % (repo, workflow, pagina, filtro))
        tutti += d.get("workflow_runs", [])
        if len(d.get("workflow_runs", [])) < 100:
            break
        pagina += 1
    return tutti


def tutti_i_run(repo=REPO):
    tutti, pagina = [], 1
    filtro = urllib.parse.quote("%s..%s" % (z(INIZIO), z(FINE)))
    while pagina <= 10:
        d = gh("repos/%s/actions/runs?per_page=100&page=%d&created=%s" % (repo, pagina, filtro))
        tutti += d.get("workflow_runs", [])
        if len(d.get("workflow_runs", [])) < 100:
            break
        pagina += 1
    return tutti


_log_cache = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def scarica_log(job_id):
    """GitHub risponde 302 verso un archivio esterno: lo si segue SENZA il token."""
    req = urllib.request.Request("https://api.github.com/repos/%s/actions/jobs/%d/logs" % (REPO, job_id),
                                 headers={"User-Agent": UA, "Authorization": "Bearer " + GH_TOKEN,
                                          "Accept": "application/vnd.github+json"})
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=30)
        raise RuntimeError("log senza redirect")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307):
            raise RuntimeError("log %s" % e.code)
        dove = e.headers["Location"]
    st, _, corpo = richiesta(dove, timeout=60)
    if st != 200:
        raise RuntimeError("log %s" % st)
    return corpo


def log_run(run):
    """Testo del log del (primo) job del run, ripulito da orari e colori."""
    rid = run["id"]
    if rid in _log_cache:
        return _log_cache[rid]
    testo = ""
    try:
        jobs = gh("repos/%s/actions/runs/%d/jobs" % (REPO, rid)).get("jobs", [])
        for j in jobs:
            grezzo = scarica_log(j["id"])
            grezzo = re.sub(r"\x1b\[[0-9;]*m", "", grezzo)
            testo += "\n".join(re.sub(r"^\S+Z ", "", r) for r in grezzo.splitlines()) + "\n"
    except Exception as e:  # log scaduti o non leggibili
        testo = "LOG-NON-LEGGIBILE %s" % e
    _log_cache[rid] = testo
    return testo


def file_al(percorso, quando):
    """Contenuto JSON del file nell'ultimo commit prima di `quando` (None se non c'e')."""
    c = gh("repos/%s/commits?path=%s&until=%s&per_page=1" % (REPO, percorso, z(quando)))
    if not c:
        return None, None
    sha, data = c[0]["sha"], iso(c[0]["commit"]["committer"]["date"])
    grezzo = gh("https://raw.githubusercontent.com/%s/%s/%s" % (REPO, sha, percorso), grezzo=True)
    return json.loads(grezzo), data


def commit_nel_giorno(percorso):
    c = gh("repos/%s/commits?path=%s&since=%s&until=%s&per_page=100" % (REPO, percorso, z(INIZIO), z(FINE)))
    return [iso(x["commit"]["committer"]["date"]) for x in c]


# ---------------------------------------------------------------- 1. live
def controlla_live():
    runs = run_del_giorno("live.yml")
    lavori = [r for r in runs if r.get("conclusion") in ("success", "failure") and r.get("run_started_at")]
    falliti = [r for r in runs if r.get("conclusion") == "failure"]
    # copertura: i cicli girano dalle 7 alle 21 (ora di Roma); buco = piu' di 30 min senza un ciclo attivo
    finestra_a = dt.datetime.combine(G, dt.time(7, 0), ROMA)
    finestra_b = dt.datetime.combine(G, dt.time(21, 0), ROMA)
    intervalli = sorted((iso(r["run_started_at"]), iso(r["updated_at"])) for r in lavori)
    buchi, cursore = [], finestra_a
    for a, b in intervalli:
        if b < cursore:
            continue
        if a > cursore and (min(a, finestra_b) - cursore).total_seconds() > 1800:
            buchi.append((cursore, min(a, finestra_b)))
        cursore = max(cursore, b)
        if cursore >= finestra_b:
            break
    if cursore < finestra_b and (finestra_b - cursore).total_seconds() > 1800:
        buchi.append((cursore, finestra_b))
    worker = [r for r in runs if r.get("event") == "workflow_dispatch"
              and (r.get("triggering_actor") or {}).get("login") == "cronoaste-eng"]
    motivo = "%d cicli, %d falliti" % (len(lavori), len(falliti))
    if buchi:
        b = ", ".join("%s-%s" % (ora_roma(a), ora_roma(c)) for a, c in buchi[:4])
        esito("Ciclo live", ERR, motivo + "; fermo: " + b)
    elif falliti:
        esito("Ciclo live", ATT, motivo)
    else:
        esito("Ciclo live", OK, motivo + ", nessun buco >30 min tra 7 e 21")
    # live.json scritto nel giorno
    c = commit_nel_giorno("data/live.json")
    if not c:
        esito("live.json", ERR, "mai aggiornato nel giorno")
    else:
        esito("live.json", OK, "%d aggiornamenti, ultimo %s" % (len(c), ora_roma(max(c))))
    # Worker avvio-workflows: lanci del live col token di cronoaste-eng
    if worker:
        esito("Worker avvio-workflows", OK, "%d lanci del live (ultimo %s)" % (len(worker), ora_roma(max(iso(r["created_at"]) for r in worker))))
    else:
        esito("Worker avvio-workflows", ERR, "nessun lancio del live dal Worker nel giorno")
    # Worker live-cronoaste (adesso)
    probl = []
    for p in ("/live.json", "/sito.json"):
        try:
            st, _, corpo = richiesta(WORKER + p, timeout=20)
            if st != 200:
                probl.append("%s %s" % (p, st))
            else:
                json.loads(corpo)
        except Exception as e:
            probl.append("%s %s" % (p, type(e).__name__))
    try:
        st, _, _ = richiesta(WORKER + "/live-completo.json", timeout=20)
        if st != 401:
            probl.append("/live-completo.json senza chiave risponde %s (atteso 401)" % st)
    except Exception as e:
        probl.append("/live-completo.json %s" % type(e).__name__)
    esito("Worker live-cronoaste", ERR if probl else OK, "; ".join(probl) if probl else "/live.json e /sito.json 200, chiave richiesta")


# ---------------------------------------------------------------- 2. scraper
def controlla_scraper():
    runs = run_del_giorno("aggiorna.yml")
    ok = [r for r in runs if r.get("conclusion") == "success"]
    ko = [r for r in runs if r.get("conclusion") == "failure"]
    lotti_fine, t_lotti = file_al("data/lotti.json", FINE)
    lotti_inizio, _ = file_al("data/lotti.json", INIZIO)
    arch_fine, _ = file_al("data/archivio.json", FINE)
    arch_inizio, _ = file_al("data/archivio.json", INIZIO)
    n_fine = len((lotti_fine or {}).get("lotti", []))
    n_inizio = len((lotti_inizio or {}).get("lotti", []))
    a_fine = len((arch_fine or {}).get("lotti", []))
    a_inizio = len((arch_inizio or {}).get("lotti", []))
    agg = (lotti_fine or {}).get("aggiornato", "")
    try:
        t_agg = iso(agg) if agg else t_lotti
    except ValueError:
        t_agg = t_lotti
    motivo = "%d run (%d falliti); lotti %d->%d, archivio %d->%d; ultimo aggiornamento %s" % (
        len(runs), len(ko), n_inizio, n_fine, a_inizio, a_fine, ora_roma(t_agg) if t_agg else "?")
    stato = OK
    if n_fine == 0 or a_fine < a_inizio:
        stato = ERR
    elif t_agg is None or t_agg < dt.datetime.combine(G, dt.time(18, 0), ROMA):
        stato = ERR
    elif n_inizio and n_fine < 0.7 * n_inizio:
        stato = ATT
        motivo += " (calo >30%)"
    elif t_agg < dt.datetime.combine(G, dt.time(21, 0), ROMA) or len(ok) < 16 or ko:
        stato = ATT
    esito("Scraper (lotti/archivio)", stato, motivo)
    v = commit_nel_giorno("data/visite.json")
    esito("visite.json", OK if v else ATT, ("%d aggiornamenti, ultimo %s" % (len(v), ora_roma(max(v)))) if v else "mai aggiornato nel giorno")


# ---------------------------------------------------------------- 3. bollettino
def controlla_bollettino():
    indice, _ = file_al("data/bollettini/index.json", FINE)
    date = sorted({b.get("data", "") for b in (indice or [])})
    ultima = date[-1] if date else ""
    if G.weekday() == 2:
        if G.isoformat() in date:
            esito("Bollettino", OK, "inviato oggi (mercoledi), in archivio")
        else:
            esito("Bollettino", ERR, "mercoledi senza bollettino (ultimo: %s)" % (ultima or "nessuno"))
        return
    if not ultima:
        esito("Bollettino", ERR, "archivio vuoto")
        return
    eta = (G - dt.date.fromisoformat(ultima)).days
    if eta <= 7:
        esito("Bollettino", OK, "ultimo %s (%d giorni fa)" % (ultima, eta))
    else:
        esito("Bollettino", ERR, "ultimo %s, %d giorni fa (atteso ogni mercoledi)" % (ultima, eta))


# ---------------------------------------------------------------- 4. WhatsApp
def controlla_whatsapp():
    if not FERIALE:
        esito("WhatsApp (email mattino)", OK, "non prevista (%s)" % GIORNI[G.weekday()])
        return
    runs = [r for r in run_del_giorno("whatsapp.yml") if r.get("conclusion") == "success"]
    inviate, nessuno, prova = 0, 0, 0
    for r in runs:
        t = log_run(r)
        inviate += len(re.findall(r"^Email inviata con \d+ beni", t, re.M))
        nessuno += len(re.findall(r"^Nessun bene nuovo", t, re.M))
        prova += len(re.findall(r"^Email di prova inviata", t, re.M))
    extra = (" (+%d di prova)" % prova) if prova else ""
    if inviate == 1:
        esito("WhatsApp (email mattino)", OK, "inviata una volta" + extra)
    elif inviate > 1:
        esito("WhatsApp (email mattino)", ERR, "inviata %d volte (doppione)%s" % (inviate, extra))
    elif nessuno:
        esito("WhatsApp (email mattino)", OK, "nessun bene nuovo, nessuna email" + extra)
    else:
        esito("WhatsApp (email mattino)", ERR, "nessuna email inviata (%d run riusciti)%s" % (len(runs), extra))


# ---------------------------------------------------------------- 5. social
def controlla_social():
    registro, _ = file_al("data/social_log.json", FINE)
    registro = registro or []
    ids = [x.get("id") for x in registro]
    def data_g(x):
        try:
            return x.get("facebook") and iso(x["facebook"]).astimezone(ROMA).date() == G
        except ValueError:
            return False
    del_g = {x.get("id") for x in registro if data_g(x)}
    doppi = sorted({i for i in ids if ids.count(i) > 1 and i in del_g})  # solo doppioni che toccano il giorno
    def del_giorno(campo):
        n = 0
        for x in registro:
            v = x.get(campo)
            if v:
                try:
                    if iso(v).astimezone(ROMA).date() == G:
                        n += 1
                except ValueError:
                    pass
        return n
    fb, ig = del_giorno("facebook"), del_giorno("instagram")
    runs = [r for r in run_del_giorno("social.yml") if r.get("conclusion") in ("success", "failure")]
    errori, in_attesa, send = 0, None, 0
    for r in runs:
        t = log_run(r)
        if "SOCIAL_MODE: send" in t:
            send += 1
        errori += len(re.findall(r"^ERRORE (Facebook|Instagram)", t, re.M))
        m = re.findall(r"^Beni nuovi: (\d+); oggi gia' pubblicati: (\d+); questa volta: (\d+)", t, re.M)
        if m:
            in_attesa = int(m[-1][0])
    motivo = "FB %d, IG %d post; %d run (send %d)" % (fb, ig, len(runs), send)
    if doppi:
        esito("Social FB/IG", ERR, motivo + "; doppioni nel registro: " + ", ".join(doppi[:5]))
    elif fb > 8 or ig > 8:
        esito("Social FB/IG", ERR, motivo + "; superato il tetto di 8")
    elif not FERIALE:
        esito("Social FB/IG", OK if fb == 0 else ATT, motivo + " (giorno festivo: non previsto)")
    elif fb == 0 and (in_attesa or 0) > 0:
        esito("Social FB/IG", ERR, motivo + "; %d beni in attesa ma nessun post" % in_attesa)
    elif fb == 0 and not runs:
        esito("Social FB/IG", ERR, motivo + "; il workflow non e' mai partito")
    elif errori or fb != ig:
        esito("Social FB/IG", ATT, motivo + ("; %d errori nei log" % errori if errori else "; FB e IG diversi"))
    else:
        esito("Social FB/IG", OK, motivo + ("; in attesa %d" % in_attesa if in_attesa else ""))
    c = commit_nel_giorno("data/social_insights.json")
    esito("Statistiche social", OK if c else ATT, ("aggiornate %s" % ora_roma(max(c))) if c else "social_insights.json non aggiornato nel giorno")


# ---------------------------------------------------------------- 6. Telegram
def controlla_telegram():
    fine, _ = file_al("data/inviati.json", FINE)
    inizio, _ = file_al("data/inviati.json", INIZIO)
    lotti, _ = file_al("data/lotti.json", FINE)
    set_fine = set((fine or {}).get("id", []))
    set_inizio = set((inizio or {}).get("id", []))
    mandati = len(set_fine - set_inizio)
    attivi = {str(l.get("id")) for l in (lotti or {}).get("lotti", [])}
    attesa = len(attivi - set_fine)
    errori = 0
    for r in run_del_giorno("aggiorna.yml"):
        if r.get("conclusion") in ("success", "failure"):
            pass  # i log orari sono tanti: si contano solo gli errori dei run falliti
        if r.get("conclusion") == "failure":
            errori += len(re.findall(r"Invio non riuscito", log_run(r)))
    motivo = "canale: %d beni nuovi inviati" % mandati + ("; %d in attesa" % attesa if attesa else "")
    if errori:
        esito("Telegram canale", ERR, motivo + "; %d invii non riusciti" % errori)
    elif FERIALE and attesa > 2 and mandati == 0:
        esito("Telegram canale", ERR, motivo + " e nessun invio")
    elif attesa > 10:
        esito("Telegram canale", ATT, motivo + " (arretrato: 2 per giro dello scraper)")
    else:
        esito("Telegram canale", OK, motivo)
    runs = run_del_giorno("eventi-telegram.yml")
    ok = [r for r in runs if r.get("conclusion") == "success"]
    if len(ok) == 1:
        esito("Riepilogo eventi (privato)", OK, "inviato alle %s" % ora_roma(iso(ok[0]["updated_at"])))
    elif len(ok) > 1:
        esito("Riepilogo eventi (privato)", ATT, "inviato %d volte" % len(ok))
    else:
        esito("Riepilogo eventi (privato)", ERR, "non inviato (%d run, nessuno riuscito)" % len(runs))


# ---------------------------------------------------------------- 7. promemoria
def controlla_promemoria():
    runs = [r for r in run_del_giorno("promemoria.yml") if r.get("conclusion") in ("success", "failure")]
    veri = []
    for r in runs:
        for m in re.finditer(r"^\{\"ok\".*\}$", log_run(r), re.M):
            try:
                d = json.loads(m.group(0))
            except ValueError:
                continue
            if d.get("prova") is False:
                veri.append(d)
    if not veri:
        esito("Promemoria email", ERR, "nessun giro reale nel giorno (%d run)" % len(runs))
        return
    inv = sum(d.get("inviati", 0) for d in veri)
    err = sum(d.get("errori", 0) for d in veri)
    es = sum(d.get("esaminati", 0) for d in veri)
    motivo = "%d giri; esaminati %d, inviati %d, errori %d" % (len(veri), es, inv, err)
    esito("Promemoria email", ERR if err else (ATT if len(veri) > 1 else OK), motivo)


# ---------------------------------------------------------------- 8. sito
def controlla_sito():
    probl, pagine = [], ["/"]
    try:
        st, _, xml = richiesta(SITO + "/sitemap.xml")
        pagine += [urllib.parse.urlparse(u).path for u in re.findall(r"<loc>([^<]+)</loc>", xml)]
    except Exception as e:
        probl.append("sitemap %s" % type(e).__name__)
    pagine = list(dict.fromkeys(pagine + ["/accedi/", "/account/"]))
    intest = {}
    for p in pagine:
        try:
            st, h, _ = richiesta(SITO + p, timeout=20)
            if st != 200:
                probl.append("%s %s" % (p, st))
            if p == "/":
                intest = {k.lower(): v for k, v in h.items()}
        except Exception as e:
            probl.append("%s %s" % (p, type(e).__name__))
    for nome in ("content-security-policy", "strict-transport-security"):
        if nome not in intest:
            probl.append("manca %s" % nome)
    if "content-security-policy-report-only" in intest and "content-security-policy" not in intest:
        probl.append("CSP solo report-only")
    esito("Sito: pagine e header", ERR if probl else OK, "; ".join(probl) if probl else "%d pagine 200, CSP e HSTS presenti" % len(pagine))
    # certificato
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(("cronoaste.cloud", 443), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname="cronoaste.cloud") as ss:
                fine = dt.datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z")
        giorni = (fine.date() - dt.date.today()).days
        esito("Certificato HTTPS", OK if giorni > 14 else (ATT if giorni > 5 else ERR),
              "scade tra %d giorni (rinnovo automatico Cloudflare)" % giorni)
    except Exception as e:
        esito("Certificato HTTPS", ERR, "non leggibile: %s" % type(e).__name__)
    # API (Pages Functions): senza chiave deve rispondere 401
    try:
        st, _, _ = richiesta(SITO + "/api/eventi", timeout=20)
        esito("Sito: /api/", OK if st == 401 else ERR, "/api/eventi senza chiave = %s%s" % (st, "" if st == 401 else " (atteso 401)"))
    except Exception as e:
        esito("Sito: /api/", ERR, "non risponde: %s" % type(e).__name__)
    # ultimo deploy Cloudflare Pages (check del commit su GitHub)
    try:
        c = gh("repos/%s/commits/main" % REPO_SITO)
        cr = gh("repos/%s/commits/%s/check-runs" % (REPO_SITO, c["sha"])).get("check_runs", [])
        cf = [x for x in cr if "Cloudflare Pages" in x.get("name", "")]
        if not cf:
            esito("Ultimo deploy Pages", ATT, "nessun esito Cloudflare per %s" % c["sha"][:7])
        else:
            x = cf[0]
            st = x.get("conclusion") or x.get("status")
            esito("Ultimo deploy Pages", OK if st == "success" else ERR, "commit %s: %s" % (c["sha"][:7], st))
    except Exception as e:
        esito("Ultimo deploy Pages", NV, str(e))


# ---------------------------------------------------------------- 9. Brevo
def controlla_brevo():
    chiave = os.environ.get("BREVO_API_KEY", "")
    if not chiave:
        esito("Brevo", NV, "chiave non disponibile in questa esecuzione")
        return
    h = {"api-key": chiave, "Accept": "application/json"}
    try:
        st, _, corpo = richiesta("https://api.brevo.com/v3/smtp/statistics/aggregatedReport?startDate=%s&endDate=%s" % (G, G), h)
        if st != 200:
            esito("Brevo", ERR, "statistiche: risposta %s" % st)
            return
        d = json.loads(corpo)
        richieste = d.get("requests", 0)
        rimbalzi = d.get("hardBounces", 0) + d.get("softBounces", 0)
        bloccate = d.get("blocked", 0) + d.get("invalid", 0)
        spam = d.get("spamReports", 0)
        credito = ""
        st2, _, corpo2 = richiesta("https://api.brevo.com/v3/account", h)
        if st2 == 200:
            for p in json.loads(corpo2).get("plan", []):
                if p.get("creditsType") == "sendLimit" or p.get("type") == "free":
                    credito = "; crediti rimasti %s" % p.get("credits")
        motivo = "email transazionali %d/300, rimbalzi %d, bloccate %d, spam %d%s" % (richieste, rimbalzi, bloccate, spam, credito)
        stato = OK
        if richieste >= 270 or spam:
            stato = ERR
        elif richieste >= 200 or rimbalzi or bloccate:
            stato = ATT
        esito("Brevo", stato, motivo)
    except Exception as e:
        esito("Brevo", ERR, "non leggibile: %s" % type(e).__name__)


# ---------------------------------------------------------------- 10. sicurezza
def controlla_sicurezza():
    falliti = []
    for repo in (REPO, REPO_SITO):
        try:
            for r in tutti_i_run(repo):
                if r.get("conclusion") in ("failure", "timed_out", "startup_failure"):
                    falliti.append("%s %s" % (r.get("name"), ora_roma(iso(r["created_at"]))))
        except Exception as e:
            falliti.append("%s non letto (%s)" % (repo, e))
    if falliti:
        esito("Run falliti (tutti i workflow)", ATT, "%d: %s" % (len(falliti), ", ".join(falliti[:6])))
    else:
        esito("Run falliti (tutti i workflow)", OK, "nessuno")
    oggi = dt.datetime.now(ROMA).date()
    vicine = []
    stato = OK
    for nome, quando in SCADENZE:
        gg = (quando - oggi).days
        if gg <= 7:
            stato = ERR
            vicine.append("%s tra %d giorni" % (nome, gg))
        elif gg <= 30:
            stato = ATT if stato == OK else stato
            vicine.append("%s tra %d giorni" % (nome, gg))
    prossima = min(SCADENZE, key=lambda x: x[1])
    esito("Scadenze token/dominio", stato, "; ".join(vicine) if vicine else "prossima: %s il %s" % (prossima[0], prossima[1].strftime("%d/%m/%Y")))
    try:
        st, _, corpo = richiesta("https://api.github.com/repos/%s/dependabot/alerts?state=open&per_page=100" % REPO,
                                 {"Authorization": "Bearer " + GH_TOKEN, "Accept": "application/vnd.github+json"})
        if st == 200:
            n = len(json.loads(corpo))
            esito("Avvisi Dependabot", OK if n == 0 else ATT, "%d avvisi aperti" % n)
        else:
            esito("Avvisi Dependabot", NV, "il token del workflow non li puo' leggere (%s)" % st)
    except Exception as e:
        esito("Avvisi Dependabot", NV, type(e).__name__)


# ---------------------------------------------------------------- 11. account (sito)
def controlla_account():
    chiave = os.environ.get("CHIAVE_PROMEMORIA", "")
    if not chiave:
        esito("Account e accessi", NV, "chiave non disponibile in questa esecuzione")
        return
    try:
        st, _, corpo = richiesta(SITO + "/api/stato?giorno=%s" % G, {"X-Chiave-Promemoria": chiave}, timeout=20)
    except Exception as e:
        esito("Account e accessi", ERR, "non risponde: %s" % type(e).__name__)
        return
    if st == 404:
        esito("Account e accessi", NV, "/api/stato non ancora online")
        return
    if st != 200:
        esito("Account e accessi", ERR, "/api/stato risponde %s" % st)
        return
    d = json.loads(corpo)
    motivo = "utenti %s (+%s nuovi, %s con Google), sessioni attive %s, preferiti %s; link richiesti %s, accessi %s, Google %s; promemoria attivi %s, inviati %s; limiti scattati %s" % (
        d.get("utenti"), d.get("utenti_nuovi"), d.get("utenti_google"), d.get("sessioni_attive"), d.get("preferiti"),
        d.get("link_creati"), d.get("link_usati"), d.get("accessi_google"), d.get("promemoria_attivi"),
        d.get("promemoria_inviati"), d.get("limiti_scattati"))
    stato = OK
    if (d.get("tetto_giorno_utc") or 0) >= 50:
        stato = ERR  # vicino al tetto di 60 email di accesso al giorno
        motivo += "; tetto giornaliero %s/60" % d.get("tetto_giorno_utc")
    elif (d.get("limiti_scattati") or 0) > 0 or (d.get("tetto_giorno_utc") or 0) >= 20:
        stato = ATT
    esito("Account e accessi", stato, motivo)


# ---------------------------------------------------------------- report
CONTROLLI = [controlla_live, controlla_scraper, controlla_bollettino, controlla_whatsapp, controlla_social,
             controlla_telegram, controlla_promemoria, controlla_sito, controlla_brevo, controlla_sicurezza,
             controlla_account]


def componi():
    n = {k: sum(1 for r in risultati if r[1] == k) for k in (OK, ATT, ERR, NV)}
    titolo = "Crono Aste - controllo di %s %s" % (GIORNI[G.weekday()], G.strftime("%d/%m/%Y"))
    if n[ERR]:
        sintesi = "%s %d ERRORI, %d attenzioni, %d ok" % (ICONA[ERR], n[ERR], n[ATT], n[OK])
    elif n[ATT]:
        sintesi = "%s %d attenzioni, %d ok" % (ICONA[ATT], n[ATT], n[OK])
    else:
        sintesi = "%s tutto ok (%d voci)" % (ICONA[OK], n[OK])
    righe = [titolo, sintesi, ""]
    ordine = {ERR: 0, ATT: 1, NV: 2, OK: 3}
    for voce, stato, motivo in sorted(risultati, key=lambda r: ordine[r[1]]):
        righe.append("%s %s: %s" % (ICONA[stato], voce, motivo))
    return "\n".join(righe)[:4000]


def gia_fatto_oggi():
    """In modalita' send: un altro run riuscito del controllo nelle ultime 20 ore = non rimandare."""
    try:
        da = dt.datetime.now(UTC) - dt.timedelta(hours=20)
        d = gh("repos/%s/actions/workflows/controllo-notturno.yml/runs?per_page=20&created=%s" % (REPO, urllib.parse.quote(">=" + z(da))))
        mio = os.environ.get("GITHUB_RUN_ID", "")
        for r in d.get("workflow_runs", []):
            if str(r["id"]) != mio and r.get("conclusion") == "success" and "send" in (r.get("display_title") or ""):
                return True
    except Exception:
        pass
    return False


def invia_telegram(testo):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID_PRIVATA", "")
    if not tok or not chat:
        print("Telegram: manca TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID_PRIVATA")
        return False
    # Destinatario: SOLO la chat privata. Se fosse un canale/gruppo (id che inizia con -), non invio.
    if chat.strip().startswith("-"):
        print("Telegram: il destinatario non e' una chat privata, non invio.")
        return False
    dati = urllib.parse.urlencode({"chat_id": chat, "text": testo, "disable_web_page_preview": "true"}).encode()
    st, _, corpo = richiesta("https://api.telegram.org/bot%s/sendMessage" % tok, dati=dati, metodo="POST")
    if st == 200 and json.loads(corpo).get("result", {}).get("chat", {}).get("type") == "private":
        print("Telegram: report inviato alla chat privata.")
        return True
    print("Telegram: invio non riuscito (%s)" % st)
    return False


def invia_email(testo):
    chiave, a, da = os.environ.get("BREVO_API_KEY", ""), os.environ.get("NOTIFY_EMAIL", ""), os.environ.get("SENDER_EMAIL", "")
    if not (chiave and a and da):
        print("Email: mancano BREVO_API_KEY, NOTIFY_EMAIL o SENDER_EMAIL")
        return False
    corpo = json.dumps({"sender": {"email": da, "name": "Controllo Crono Aste"}, "to": [{"email": a}],
                        "subject": testo.splitlines()[0] + " (Telegram non raggiungibile)",
                        "textContent": testo}).encode()
    st, _, _ = richiesta("https://api.brevo.com/v3/smtp/email", {"api-key": chiave, "Content-Type": "application/json"}, corpo, "POST")
    print("Email: %s" % ("inviata" if st in (200, 201) else "non riuscita (%s)" % st))
    return st in (200, 201)


def main():
    if MODE not in ("prova", "send"):
        print("CONTROLLO_MODE deve essere prova o send")
        return 2
    if MODE == "send" and gia_fatto_oggi():
        print("Report gia' inviato nelle ultime 20 ore: non lo rimando.")
        return 0
    for f in CONTROLLI:
        try:
            f()
        except Exception as e:
            esito(f.__name__.replace("controlla_", "Controllo ").capitalize(), ERR, "controllo non riuscito: %s" % e)
    illeggibili = sum(1 for t in _log_cache.values() if t.startswith("LOG-NON-LEGGIBILE"))
    if illeggibili:
        esito("Log dei run", NV, "%d log non leggibili: le voci che li usano possono essere sbagliate" % illeggibili)
    testo = componi()
    print(testo)
    if MODE == "prova":
        print("\n(modalita' prova: nessun invio)")
        return 0
    if invia_telegram(testo) or invia_email(testo):
        return 0
    return 1  # il run fallisce: il ciclo live lo rilancia


if __name__ == "__main__":
    sys.exit(main())
