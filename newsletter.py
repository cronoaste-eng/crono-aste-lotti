#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bollettino settimanale Crono Aste: email (Brevo) + archivio + PDF A4.

MODALITA (variabile NEWSLETTER_MODE):
  dry   = (predefinita) prepara i file in out/, NON invia niente, NON scrive l'archivio
  test  = come dry, in piu manda UNA prova a TEST_EMAIL tramite Brevo
  send  = invia alla lista Brevo e salva l'archivio in data/bollettini/
Chiavi e dati personali arrivano SOLO da variabili d'ambiente (segreti GitHub).
"""
import os, sys, json, html, io, datetime as dt
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")
E = os.environ.get
MODE = (E("NEWSLETTER_MODE") or "dry").strip().lower()
FORCE = E("NEWSLETTER_FORCE") == "1"
LOTTI = E("LOTTI_PATH", "data/lotti.json")
OUT = E("OUT_DIR", "out")
ARCH = E("ARCHIVE_DIR", "data/bollettini")
STATO = E("STATE_PATH", "data/newsletter_stato.json")
VETRINA = E("VETRINA_URL", "https://www.cronoaste.cloud")
SENDER_NAME = E("SENDER_NAME", "Crono Aste")
SENDER_EMAIL = E("SENDER_EMAIL", "")
REPLY_TO = E("REPLY_TO", "")
TEST_EMAIL = E("TEST_EMAIL", "")
SEDE = E("SEDE_LEGALE", "[indirizzo sede]")
PIVA = E("PARTITA_IVA", "[partita IVA]")
BREVO_KEY = E("BREVO_API_KEY", "")
BREVO_LIST = E("BREVO_LIST_ID", "")
TOT = 12          # beni totali per bollettino
MAX_NUOVI = 3     # beni "nuovi" con foto grande
MAX_SCAD = 3      # beni in scadenza
GIORNI_NUOVO = 7
GIORNI_SCAD = 10

NAVY, TEAL, TEAL_D, INK2, LINE, SOFT = "#14325c", "#0f9d94", "#0b7a73", "#4a5d78", "#dde5ee", "#f3f8fc"
MESI = ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto",
        "settembre","ottobre","novembre","dicembre"]


# ---------- utilita ----------
def esc(s):
    return html.escape(str(s or ""), quote=True)

def okurl(u):
    try:
        p = urlparse(u)
    except Exception:
        return ""
    if p.scheme != "https":
        return ""
    h = (p.hostname or "").lower()
    if h == "cronoaste.fallcoaste.it" or h.endswith(".cloudfront.net"):
        return u
    return ""

def pdt(s):
    try:
        return dt.datetime.strptime(s, "%d/%m/%Y %H:%M").replace(tzinfo=ROMA)
    except Exception:
        return None

def euro(x):
    s = f"{x:,.2f}"
    return "\u20ac " + s.replace(",", "X").replace(".", ",").replace("X", ".")

def corto(t, n=95):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip(" ,.;") + "\u2026"

def deposito(l):
    c = [pdt(l.get(k)) for k in ("termine_carta", "termine_bonifico")]
    c = [x for x in c if x]
    if not c:
        t = pdt(l.get("termine"))
        c = [t] if t else []
    return min(c) if c else None

def data_it(d):
    return d.strftime("%d/%m/%Y") + " ore " + d.strftime("%H:%M")

def prezzo(l):
    if l.get("prezzo_minimo"):
        return "Offerta minima", euro(l["prezzo_minimo"])
    return (l.get("etichetta") or "Prezzo base"), euro(l.get("prezzo") or 0)

def dato(l, ora):
    """Dati minimi del bene, salvati nell'archivio."""
    et, pr = prezzo(l)
    ini = pdt(l.get("inizio"))
    dep = deposito(l)
    pub = pdt(l.get("pubblicata"))
    return {
        "id": str(l.get("id", "")),
        "titolo": corto(l.get("titolo")),
        "tribunale": l.get("tribunale") or "",
        "etichetta": et,
        "prezzo": pr,
        "inizio": data_it(ini) if ini else "",
        "deposito": data_it(dep) if dep else "",
        "foto": okurl(l.get("foto") or ""),
        "url": okurl(l.get("url") or ""),
        "nuovo": bool(pub and (ora - pub) <= dt.timedelta(days=GIORNI_NUOVO)),
    }


# ---------- selezione ----------
def scegli(lotti, ora, stato):
    attivi = []
    for l in lotti:
        if l.get("stato") != "attiva":
            continue
        ini = pdt(l.get("inizio"))
        dep = deposito(l)
        if not ini or ini <= ora:
            continue
        if dep and dep <= ora:
            continue
        attivi.append(l)
    usati = set()

    nuovi = [l for l in attivi if pdt(l.get("pubblicata")) and
             (ora - pdt(l["pubblicata"])) <= dt.timedelta(days=GIORNI_NUOVO)]
    nuovi.sort(key=lambda l: pdt(l["pubblicata"]), reverse=True)
    nuovi_tutti_l = list(nuovi)
    nuovi_tutti = len(nuovi)
    nuovi = nuovi[:MAX_NUOVI]
    usati |= {l["id"] for l in nuovi}

    scad = [l for l in attivi if l["id"] not in usati and deposito(l) and
            deposito(l) - ora <= dt.timedelta(days=GIORNI_SCAD)]
    scad.sort(key=deposito)
    scad = scad[:MAX_SCAD]
    usati |= {l["id"] for l in scad}

    mancano = max(0, TOT - len(nuovi) - len(scad))
    visti = stato.get("mostrati", {})
    resto = [l for l in attivi if l["id"] not in usati]
    # prima i mai mostrati / mostrati da piu tempo; a pari merito inizio asta piu vicino
    nuovo_id = {l["id"] for l in nuovi_tutti_l}
    resto.sort(key=lambda l: (l["id"] not in nuovo_id, visti.get(l["id"], ""), pdt(l["inizio"])))
    altri = resto[:mancano]
    return nuovi, scad, altri, len(attivi), nuovi_tutti


# ---------- email ----------
def foto_cell(d, w, h, alt):
    if d["foto"]:
        return ('<img src="%s" width="%d" height="%d" alt="%s" style="display:block;width:%dpx;height:%dpx;'
                'object-fit:cover;border:0;border-radius:8px;background:#eaf2fa">'
                % (esc(d["foto"]), w, h, esc(alt), w, h))
    return ('<div style="width:%dpx;height:%dpx;border-radius:8px;background:#eaf2fa;'
            'text-align:center;line-height:%dpx;color:#8aa4c4;font-size:12px">Foto non disponibile</div>' % (w, h, h))

def link(d, testo):
    if d["url"]:
        return '<a href="%s" style="color:%s;text-decoration:none">%s</a>' % (esc(d["url"]), NAVY, testo)
    return testo

def badge_nuovo():
    return ('<span style="display:inline-block;background:%s;color:#ffffff;font-size:12px;font-weight:bold;'
            'padding:3px 10px;border-radius:999px">Nuovo</span>' % TEAL)

def card_grande(d):
    return f'''<tr><td style="padding:0 24px 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:12px;border-collapse:separate">
<tr><td style="padding:0">{foto_cell(d, 550, 200, d["titolo"])}</td></tr>
<tr><td style="padding:14px 16px">
<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{TEAL_D};font-weight:bold">Trib. {esc(d["tribunale"])} &nbsp;{badge_nuovo() if d["nuovo"] else ""}</div>
<div style="font-size:16px;line-height:1.4;font-weight:bold;color:{NAVY};margin-top:6px">{link(d, esc(d["titolo"]))}</div>
<div style="font-size:13px;line-height:1.6;color:{INK2};margin-top:6px">Inizio asta: {esc(d["inizio"])}<br>Deposito offerte entro: {esc(d["deposito"])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;border-top:1px solid #e3eaf2"><tr>
<td style="padding-top:10px;font-size:13px;color:{INK2}">{esc(d["etichetta"])}</td>
<td align="right" style="padding-top:10px;font-size:18px;font-weight:bold;color:{NAVY}">{esc(d["prezzo"])}</td></tr></table>
</td></tr></table></td></tr>'''

def riga_scad(d, i):
    bg = SOFT if i % 2 == 0 else "#ffffff"
    return f'''<tr><td style="padding:10px 12px;background:{bg};border-top:1px solid {LINE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td width="68" valign="middle">{foto_cell(d, 56, 56, d["titolo"])}</td>
<td valign="middle" style="padding:0 10px;font-size:14px;line-height:1.4;color:{NAVY}">{link(d, esc(d["titolo"]))}<br>
<span style="font-size:12px;color:{INK2}">Trib. {esc(d["tribunale"])} &middot; deposito entro {esc(d["deposito"])}</span></td>
<td valign="middle" align="right" style="font-size:14px;font-weight:bold;color:{NAVY};white-space:nowrap">{esc(d["prezzo"])}</td>
</tr></table></td></tr>'''

def cella_altro(d):
    return f'''<td width="50%" valign="top" style="padding:0 6px 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:12px;border-collapse:separate">
<tr><td style="padding:0">{foto_cell(d, 262, 110, d["titolo"])}</td></tr>
<tr><td style="padding:10px 12px">
<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{TEAL_D};font-weight:bold">Trib. {esc(d["tribunale"])}</div>
<div style="font-size:14px;line-height:1.35;color:{NAVY};margin:3px 0 6px">{link(d, esc(d["titolo"]))}</div>
<div style="font-size:15px;font-weight:bold;color:{NAVY}">{esc(d["prezzo"])}</div>
</td></tr></table></td>'''

def titolo_sez(t):
    return f'<tr><td style="padding:20px 24px 8px;font-size:14px;font-weight:bold;color:{NAVY}">{esc(t)}</td></tr>'

def email_html(b, totale, unsub="{{ unsubscribe }}"):
    n, s, a = b["nuovi"], b["scadenze"], b["altri"]
    parti = []
    if n:
        parti.append(titolo_sez("Nuovi beni") + "".join(card_grande(d) for d in n))
    if s:
        parti.append(titolo_sez("In scadenza nei prossimi giorni") +
                     '<tr><td style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     f'style="border:1px solid {LINE};border-radius:12px;border-collapse:separate;overflow:hidden">'
                     + "".join(riga_scad(d, i) for i, d in enumerate(s)) + "</table></td></tr>")
    if a:
        righe = ""
        for i in range(0, len(a), 2):
            c = [cella_altro(a[i])]
            c.append(cella_altro(a[i + 1]) if i + 1 < len(a) else '<td width="50%"></td>')
            righe += "<tr>" + "".join(c) + "</tr>"
        parti.append(titolo_sez("Altri beni in vendita") +
                     '<tr><td style="padding:0 18px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                     + righe + "</table></td></tr>")
    mostrati_n = len(n) + sum(1 for d in a if d["nuovo"])
    intro = ((f"Questa settimana abbiamo pubblicato {b['nuovi_tot']} nuovi beni" +
              (f": qui sotto trovi i pi\u00f9 recenti. " if b["nuovi_tot"] > mostrati_n else ". ")) if b["nuovi_tot"] else
             "Questa settimana non ci sono nuovi beni, ma ecco cosa puoi ancora trovare. ")
    intro += "Qui sotto trovi le prossime scadenze e altri beni ancora in vendita."
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(b["oggetto"])}</title></head>
<body style="margin:0;padding:0;background:#eef3f8;font-family:Arial,Helvetica,sans-serif;color:{NAVY}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(b["anteprima"])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef3f8"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:12px">
<tr><td style="background:{NAVY};padding:20px 24px;border-radius:12px 12px 0 0"><span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:8px;background:#ffffff;color:{NAVY};font-weight:bold">C</span>
<span style="color:#ffffff;font-size:18px;font-weight:bold;margin-left:8px;vertical-align:middle">Crono Aste</span></td></tr>
<tr><td style="padding:24px 24px 4px"><div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{TEAL_D};font-weight:bold">Bollettino {esc(b["numero"])} - {esc(b["data_lunga"])}</div>
<div style="font-size:22px;line-height:1.3;font-weight:bold;margin:6px 0 8px">Le novit\u00e0 della settimana</div>
<div style="font-size:15px;line-height:1.6;color:{INK2}">{esc(intro)}</div></td></tr>
{"".join(parti)}
<tr><td align="center" style="padding:24px"><a href="{esc(VETRINA)}" style="display:inline-block;background:{TEAL};color:#ffffff;font-size:15px;font-weight:bold;padding:13px 24px;border-radius:12px;text-decoration:none">Vedi tutti i {totale} beni in vendita</a></td></tr>
<tr><td style="background:{SOFT};padding:18px 24px;font-size:12px;line-height:1.7;color:{INK2};text-align:center;border-radius:0 0 12px 12px">
Ricevi questa email perch\u00e9 ti sei iscritto alla newsletter di Crono Aste.<br>
Crono Aste S.r.l. Unipersonale &middot; {esc(SEDE)} &middot; P.IVA {esc(PIVA)}<br>
<a href="{unsub}" style="color:{INK2}">Annulla l'iscrizione</a> &middot; <a href="{esc(VETRINA)}" style="color:{INK2}">Privacy Policy</a></td></tr>
</table></td></tr></table></body></html>'''


# ---------- PDF A4 ----------
def scarica(url):
    if not url:
        return None
    try:
        with urlopen(Request(url, headers={"User-Agent": "crono-aste-bollettino"}), timeout=15) as r:
            return r.read()
    except Exception:
        return None

def pdf_a4(b, totale, path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
    from reportlab.lib.utils import ImageReader

    navy, teal = colors.HexColor(NAVY), colors.HexColor(TEAL)
    s_t = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=navy)
    s_k = ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.HexColor(TEAL_D))
    s_h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=navy, spaceBefore=8, spaceAfter=3)
    s_b = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#14325c"))
    s_s = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10.5, textColor=colors.HexColor(INK2))
    s_p = ParagraphStyle("p", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=navy, alignment=2)
    s_f = ParagraphStyle("f", fontName="Helvetica", fontSize=7.5, leading=10, textColor=colors.HexColor(INK2))

    def img(d):
        raw = scarica(d["foto"])
        if raw:
            try:
                ir = ImageReader(io.BytesIO(raw))
                w, h = ir.getSize()
                bw, bh = 20 * mm, 13 * mm
                k = min(bw / w, bh / h)
                return Image(io.BytesIO(raw), width=w * k, height=h * k)
            except Exception:
                pass
        return Paragraph("foto n.d.", s_s)

    def riga(d, extra):
        corpo = [Paragraph("<b>%s</b>" % esc(d["titolo"]), s_b),
                 Paragraph("Trib. %s &middot; Inizio asta %s &middot; Deposito entro %s%s" %
                           (esc(d["tribunale"]), esc(d["inizio"]), esc(d["deposito"]), extra), s_s)]
        return [img(d), corpo, Paragraph(esc(d["prezzo"]) + "<br/><font size=7 color='%s'>%s</font>" % (INK2, esc(d["etichetta"])), s_p)]

    def tabella(ds, extra=""):
        t = Table([riga(d, extra) for d in ds], colWidths=[25 * mm, 118 * mm, 33 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
        return t

    def piede(c, doc):
        c.saveState()
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor(INK2))
        c.drawString(15 * mm, 9 * mm, "Crono Aste S.r.l. Unipersonale - %s - P.IVA %s" % (SEDE, PIVA))
        c.drawRightString(195 * mm, 9 * mm, "Pagina %d" % doc.page)
        c.restoreState()

    st = [Paragraph("CRONO ASTE - BOLLETTINO %s DEL %s" % (b["numero"].upper(), b["data_lunga"].upper()), s_k),
          Paragraph("%s" % esc(b["titolo_pdf"]), s_t), Spacer(1, 4)]
    if b["nuovi"]:
        st += [Paragraph("Nuovi beni", s_h), tabella(b["nuovi"])]
    if b["scadenze"]:
        st += [Paragraph("In scadenza nei prossimi giorni", s_h), tabella(b["scadenze"])]
    if b["altri"]:
        st += [Paragraph("Altri beni in vendita", s_h), tabella(b["altri"])]
    st += [Spacer(1, 6),
           Paragraph("Dati aggiornati al %s. Prezzi e termini possono cambiare: per i beni ancora in vendita consulta %s "
                     "(tutti i %d beni)." % (b["data_lunga"], esc(VETRINA), totale), s_f)]
    SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=11 * mm, bottomMargin=15 * mm,
                      title="Bollettino Crono Aste " + b["data"], author="Crono Aste").build(st, onFirstPage=piede, onLaterPages=piede)


# ---------- Brevo ----------
def brevo(metodo, path, corpo=None):
    req = Request("https://api.brevo.com/v3" + path, method=metodo,
                  data=json.dumps(corpo).encode() if corpo is not None else None,
                  headers={"api-key": BREVO_KEY, "accept": "application/json", "content-type": "application/json"})
    try:
        with urlopen(req, timeout=60) as r:
            t = r.read().decode() or "{}"
            return json.loads(t)
    except HTTPError as e:
        print("Brevo errore", e.code, e.read().decode()[:300])
        raise

def crea_campagna(b, htm):
    corpo = {"name": "Bollettino " + b["numero"].replace("/", "-") + " " + b["data"], "subject": b["oggetto"],
             "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL}, "type": "classic",
             "htmlContent": htm, "recipients": {"listIds": [int(BREVO_LIST)]}}
    if REPLY_TO:
        corpo["replyTo"] = REPLY_TO
    return brevo("POST", "/emailCampaigns", corpo)["id"]


# ---------- principale ----------
def carica(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def salva(p, obj):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)

def main():
    ora = dt.datetime.now(ROMA)
    if MODE == "send" and not FORCE:
        if ora.weekday() != 2 or ora.hour not in (8, 9):
            print("Fuori orario (serve mercoledi, ore 8-9 italiane): niente da fare.")
            return 0
    oggi = ora.strftime("%Y-%m-%d")
    indice = carica(os.path.join(ARCH, "index.json"), [])
    if MODE == "send" and any(x.get("data") == oggi for x in indice) and not FORCE:
        print("Bollettino di oggi gia inviato: niente da fare.")
        return 0

    dati = carica(LOTTI, {})
    lotti = dati.get("lotti") or []
    if len(lotti) < 5:
        print("Dati dei lotti insufficienti o vuoti: non invio nulla.")
        return 1
    stato = carica(STATO, {})
    nuovi, scad, altri, totale, nuovi_tot = scegli(lotti, ora, stato)
    if not (nuovi or scad or altri):
        print("Nessun bene da mostrare: non invio.")
        return 0

    dl = "%d %s %d" % (ora.day, MESI[ora.month - 1], ora.year)
    iso = ora.isocalendar()
    numero = "n. %d/%d" % (iso[1], iso[0])
    p = []
    if nuovi_tot:
        p.append("%d %s" % (nuovi_tot, "novit\u00e0" if nuovi_tot != 1 else "novit\u00e0"))
    if scad:
        p.append("%d scadenz%s" % (len(scad), "e" if len(scad) != 1 else "a"))
    if altri:
        p.append("altri %d beni" % len(altri))
    oggetto = "Bollettino " + numero + " - Beni all'asta: " + ", ".join(p[:-1]) + (" e " if len(p) > 1 else "") + p[-1]
    b = {"data": oggi, "data_lunga": dl, "numero": numero, "oggetto": oggetto,
         "anteprima": "Cosa \u00e8 stato pubblicato questa settimana e cosa scade a breve",
         "titolo_pdf": "Le novit\u00e0 della settimana",
         "nuovi_tot": nuovi_tot,
         "nuovi": [dato(l, ora) for l in nuovi],
         "scadenze": [dato(l, ora) for l in scad],
         "altri": [dato(l, ora) for l in altri],
         "totale_beni": totale}

    os.makedirs(OUT, exist_ok=True)
    htm = email_html(b, totale)
    open(os.path.join(OUT, "email.html"), "w", encoding="utf-8").write(htm.replace("{{ unsubscribe }}", "#"))
    salva(os.path.join(OUT, "bollettino.json"), b)
    pdf_a4(b, totale, os.path.join(OUT, "bollettino.pdf"))
    print("Preparato:", oggetto, "| nuovi", len(nuovi), "scadenze", len(scad), "altri", len(altri), "| attivi", totale)

    if MODE == "dry":
        print("Modalita dry: nessun invio.")
        return 0
    if not (BREVO_KEY and BREVO_LIST and SENDER_EMAIL):
        print("Mancano BREVO_API_KEY, BREVO_LIST_ID o SENDER_EMAIL: non invio.")
        return 1
    cid = crea_campagna(b, htm)
    if MODE == "test":
        if not TEST_EMAIL:
            print("Manca TEST_EMAIL.")
            return 1
        brevo("POST", "/emailCampaigns/%s/sendTest" % cid, {"emailTo": [TEST_EMAIL]})
        print("Prova inviata a", TEST_EMAIL)
        try:
            brevo("DELETE", "/emailCampaigns/%s" % cid)
        except Exception:
            pass
        return 0
    if MODE == "send":
        brevo("POST", "/emailCampaigns/%s/sendNow" % cid)
        print("Campagna inviata:", cid)
        os.makedirs(ARCH, exist_ok=True)
        salva(os.path.join(ARCH, oggi + ".json"), b)
        with open(os.path.join(OUT, "bollettino.pdf"), "rb") as f, open(os.path.join(ARCH, oggi + ".pdf"), "wb") as g:
            g.write(f.read())
        indice = [x for x in indice if x.get("data") != oggi]
        indice.insert(0, {"data": oggi, "data_lunga": dl, "numero": numero, "oggetto": oggetto, "nuovi": nuovi_tot,
                          "scadenze": len(scad), "altri": len(altri), "totale": totale})
        salva(os.path.join(ARCH, "index.json"), indice)
        m = stato.get("mostrati", {})
        for d in b["nuovi"] + b["scadenze"] + b["altri"]:
            m[d["id"]] = oggi
        attivi_id = {str(l.get("id")) for l in lotti}
        stato["mostrati"] = {k: v for k, v in m.items() if k in attivi_id}
        salva(STATO, stato)
        return 0
    print("NEWSLETTER_MODE non valida:", MODE)
    return 1

if __name__ == "__main__":
    sys.exit(main())
