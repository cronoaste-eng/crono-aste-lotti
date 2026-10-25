#!/usr/bin/env python3
"""Pubblica i beni nuovi sulla Pagina Facebook e su Instagram di Crono Aste (un post per bene).

Per ogni bene nuovo: crea la cartolina 4:5 (immagine_social.py), la carica sulla Pagina Facebook
come foto con didascalia e link, poi la pubblica anche su Instagram (account professionale).
Stato (beni gia' trattati, token Instagram rinnovato) nella cache di Actions: cartella state_social,
NON nel repository. Primo avvio senza stato: segna i beni presenti come visti e non pubblica nulla.

Variabili d'ambiente:
  SOCIAL_MODE  dry (default: crea le immagini in out_social/ e stampa le didascalie, non pubblica nulla)
               uno (pubblica davvero SOLO il bene piu' recente: prova)
               send (pubblica davvero i beni nuovi, nei limiti di cadenza)
               storia (prova: pubblica davvero SOLO la Storia del bene piu' recente, senza post)
  SOCIAL_STORIE (default 1: Storie sfalsate, una per esecuzione, dal bene pubblicato in un'esecuzione precedente; 0 = solo post)
  FB_PAGE_TOKEN (segreto), FB_PAGE_ID, INSTAGRAM_TOKEN (segreto), IG_USER_ID
  SOCIAL_MAX_RUN (default 2: beni per esecuzione), SOCIAL_MAX_DAY (default 8: beni al giorno)
  SOCIAL_ORE (default 9-19 ore italiane, solo giorni feriali; SOCIAL_FORCE=1 ignora orario e giorno)
"""
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import immagine_social

BASE = Path(__file__).parent
LOTTI = BASE / "data" / "lotti.json"
STATO = BASE / "state_social" / "stato.json"
USCITA = BASE / "out_social"
MODE = (os.environ.get("SOCIAL_MODE") or "dry").strip().lower()
FORCE = os.environ.get("SOCIAL_FORCE") == "1"
STORIE = os.environ.get("SOCIAL_STORIE", "1") != "0"
STORIA_ORE_MAX = 20  # una Storia non ancora uscita dopo 20 ore dal post si scarta
MAX_RUN = int(os.environ.get("SOCIAL_MAX_RUN") or 2)
MAX_DAY = int(os.environ.get("SOCIAL_MAX_DAY") or 8)
ORE = os.environ.get("SOCIAL_ORE") or "9-19"
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN", "")
FB_PAGE = os.environ.get("FB_PAGE_ID", "")
IG_TOKEN = os.environ.get("INSTAGRAM_TOKEN", "")
IG_USER = os.environ.get("IG_USER_ID") or "17841445610038092"
GRAPH_FB = "https://graph.facebook.com"
GRAPH_IG = "https://graph.instagram.com"
PIATTAFORMA = "https://cronoaste.fallcoaste.it"
RINNOVO_GIORNI = 30   # il token Instagram dura 60 giorni: lo rinnoviamo dopo 30


def carica(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def quando(s):
    m = re.search(r"(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)", s or "")
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5))) if m else None


def scadenza(l):
    d = [quando(l.get("termine_carta")), quando(l.get("termine_bonifico")), quando(l.get("termine"))]
    d = [x for x in d if x]
    return min(d) if d else datetime(2100, 1, 1)


def euro(n):
    p = f"{float(n):.2f}".split(".")
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ".", p[0]) + "," + p[1]


def titolo_completo(l):
    """La piattaforma accorcia i titoli lunghi con "...": se serve rileggiamo il titolo intero dalla pagina del bene."""
    t = (l.get("titolo") or "").strip()
    if t.endswith("...") and l.get("url"):
        try:
            r = requests.get(l["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            m = re.search(r"<h1[^>]*>(.*?)</h1>", r.text, re.S)
            if r.ok and m:
                intero = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
                if len(intero) > len(t) - 3:
                    t = intero
        except Exception as e:  # noqa: BLE001
            print("Titolo intero non letto (%s): uso quello breve." % e, file=sys.stderr)
    t = t.rstrip(" .")
    if len(t) > 160:
        taglio = max(t.rfind(x, 60, 160) for x in (", ", "; ", " - ", ". ", ": "))
        if taglio > 0:
            t = t[:taglio].rstrip(" .,;:-")
        else:
            t = t[:157].rsplit(" ", 1)[0].rstrip(" .,;:-") + "..."
    elif (l.get("titolo") or "").strip().endswith("..."):
        t = t.rstrip(" ,;:-")
    return t


def didascalia(l, instagram):
    min_ = l.get("prezzo_minimo")
    prezzo = min_ if min_ is not None else l.get("prezzo")
    etichetta = "Offerta minima" if min_ is not None else "Prezzo base"
    testa = "Trib. %s - Proc. %s" % (l.get("tribunale") or "-", l.get("procedura") or "-")
    if l.get("lotto"):
        testa += " - " + l["lotto"]
    titolo = l.get("titolo") or ""
    righe = ["Nuovo bene in vendita", testa, "", titolo, ""]
    if prezzo is not None:
        righe.append("%s: € %s" % (etichetta, euro(prezzo)))
    dep = l.get("termine_carta") or l.get("termine_bonifico") or ""
    ini = (l.get("inizio") or "").split(" ")
    if dep:
        righe.append("Offerte entro %s" % dep)
    if ini[0]:
        righe.append("Asta il %s%s" % (ini[0], (" ore " + ini[1]) if len(ini) > 1 else ""))
    righe.append("")
    if instagram:
        righe.append("Dettagli e offerte: link in bio")
        righe.append("#asteGiudiziarie #venditeGiudiziarie #cronoaste")
    else:
        righe.append("Vedi il bene e partecipa: %s" % (l.get("url") or PIATTAFORMA))
    return "\n".join(righe)


def ora_italia():
    return datetime.now(ZoneInfo("Europe/Rome"))


def dentro_orario(adesso):
    if adesso.weekday() >= 5:
        return False
    a, b = [int(x) for x in ORE.split("-")]
    return a <= adesso.hour < b


def errore_api(r):
    try:
        e = r.json().get("error", {})
        return "%s (codice %s)" % (e.get("message", r.text[:200]), e.get("code"))
    except ValueError:
        return r.text[:200]


def pubblica_facebook(img_path, testo):
    """Carica la foto sulla Pagina (pubblicata). Restituisce l'indirizzo pubblico dell'immagine."""
    with open(img_path, "rb") as f:
        r = requests.post("%s/%s/photos" % (GRAPH_FB, FB_PAGE), data={"caption": testo, "access_token": FB_TOKEN},
                          files={"source": ("bene.jpg", f, "image/jpeg")}, timeout=90)
    if not r.ok:
        raise RuntimeError("Facebook: " + errore_api(r))
    foto_id = r.json().get("id")
    r2 = requests.get("%s/%s" % (GRAPH_FB, foto_id), params={"fields": "images", "access_token": FB_TOKEN}, timeout=30)
    if not r2.ok:
        raise RuntimeError("Facebook (immagine): " + errore_api(r2))
    imgs = r2.json().get("images") or []
    return foto_id, (imgs[0]["source"] if imgs else "")


def pubblica_instagram(url_img, testo, token):
    r = requests.post("%s/%s/media" % (GRAPH_IG, IG_USER),
                      data={"image_url": url_img, "caption": testo, "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError("Instagram (contenitore): " + errore_api(r))
    cid = r.json()["id"]
    for _ in range(12):
        s = requests.get("%s/%s" % (GRAPH_IG, cid), params={"fields": "status_code", "access_token": token}, timeout=30)
        stato = (s.json() or {}).get("status_code") if s.ok else None
        if stato == "FINISHED":
            break
        if stato in ("ERROR", "EXPIRED"):
            raise RuntimeError("Instagram: elaborazione immagine fallita (%s)" % stato)
        time.sleep(5)
    p = requests.post("%s/%s/media_publish" % (GRAPH_IG, IG_USER), data={"creation_id": cid, "access_token": token}, timeout=60)
    if not p.ok:
        raise RuntimeError("Instagram (pubblicazione): " + errore_api(p))
    return p.json().get("id")


def pubblica_storia_facebook(img_path):
    """Storia sulla Pagina: foto caricata senza pubblicarla sul diario, poi trasformata in Storia. Restituisce l'indirizzo dell'immagine."""
    with open(img_path, "rb") as f:
        r = requests.post("%s/%s/photos" % (GRAPH_FB, FB_PAGE), data={"published": "false", "access_token": FB_TOKEN},
                          files={"source": ("storia.jpg", f, "image/jpeg")}, timeout=90)
    if not r.ok:
        raise RuntimeError("Facebook storia (foto): " + errore_api(r))
    foto_id = r.json().get("id")
    r2 = requests.post("%s/%s/photo_stories" % (GRAPH_FB, FB_PAGE), data={"photo_id": foto_id, "access_token": FB_TOKEN}, timeout=60)
    if not r2.ok:
        raise RuntimeError("Facebook storia: " + errore_api(r2))
    r3 = requests.get("%s/%s" % (GRAPH_FB, foto_id), params={"fields": "images", "access_token": FB_TOKEN}, timeout=30)
    imgs = (r3.json().get("images") or []) if r3.ok else []
    return foto_id, (imgs[0]["source"] if imgs else "")


def pubblica_storia_instagram(url_img, token):
    r = requests.post("%s/%s/media" % (GRAPH_IG, IG_USER),
                      data={"image_url": url_img, "media_type": "STORIES", "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError("Instagram storia (contenitore): " + errore_api(r))
    cid = r.json()["id"]
    for _ in range(12):
        s = requests.get("%s/%s" % (GRAPH_IG, cid), params={"fields": "status_code", "access_token": token}, timeout=30)
        stato = (s.json() or {}).get("status_code") if s.ok else None
        if stato == "FINISHED":
            break
        if stato in ("ERROR", "EXPIRED"):
            raise RuntimeError("Instagram storia: elaborazione fallita (%s)" % stato)
        time.sleep(5)
    p = requests.post("%s/%s/media_publish" % (GRAPH_IG, IG_USER), data={"creation_id": cid, "access_token": token}, timeout=60)
    if not p.ok:
        raise RuntimeError("Instagram storia (pubblicazione): " + errore_api(p))
    return p.json().get("id")


def pubblica_storie(l, token_ig):
    """Una Storia su Facebook e una su Instagram per il bene. Un errore qui non ferma i post: si scrive e si va avanti."""
    img = immagine_social.crea_storia(l)
    percorso = USCITA / ("%s_storia.jpg" % l["id"])
    img.save(percorso, quality=92)
    url_img = ""
    try:
        foto_id, url_img = pubblica_storia_facebook(percorso)
        print("Storia Facebook ok: bene %s (foto %s)." % (l["id"], foto_id))
    except Exception as e:  # noqa: BLE001
        print("ATTENZIONE storia Facebook, bene %s: %s" % (l["id"], e), file=sys.stderr)
    try:
        if not (token_ig and url_img):
            raise RuntimeError("manca il token Instagram o l'immagine")
        print("Storia Instagram ok: bene %s (post %s)." % (l["id"], pubblica_storia_instagram(url_img, token_ig)))
    except Exception as e:  # noqa: BLE001
        print("ATTENZIONE storia Instagram, bene %s: %s" % (l["id"], e), file=sys.stderr)


def rinnova_token(stato):
    """Il token Instagram dura 60 giorni: dopo 30 giorni ne chiediamo uno nuovo e lo teniamo nello stato."""
    token = stato.get("ig_token") or IG_TOKEN
    ultimo = stato.get("ig_rinnovato") or ""
    try:
        giorni = (datetime.now() - datetime.fromisoformat(ultimo)).days if ultimo else 999
    except ValueError:
        giorni = 999
    if giorni < RINNOVO_GIORNI or not token:
        return token
    r = requests.get("%s/refresh_access_token" % GRAPH_IG,
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    if r.ok and r.json().get("access_token"):
        stato["ig_token"] = r.json()["access_token"]
        stato["ig_rinnovato"] = datetime.now().isoformat(timespec="seconds")
        print("Token Instagram rinnovato.")
        return stato["ig_token"]
    print("Rinnovo del token Instagram non riuscito: %s" % errore_api(r), file=sys.stderr)
    return token


def main():
    d = carica(LOTTI)
    lotti = [l for l in (d or {}).get("lotti", []) if l.get("stato") == "attiva" and l.get("id") and l.get("foto")]
    if not lotti:
        print("Nessun lotto attivo letto: non faccio nulla.")
        return 0
    stato = carica(STATO)
    if stato is None:
        print("Primo avvio: segno come visti %d beni, nessuna pubblicazione." % len(lotti))
        if MODE != "dry":
            STATO.parent.mkdir(exist_ok=True)
            STATO.write_text(json.dumps({"visti": [l["id"] for l in lotti], "giorni": {}}), encoding="utf-8")
        return 0

    adesso = ora_italia()
    oggi = adesso.strftime("%Y-%m-%d")
    if MODE == "send" and not FORCE and not dentro_orario(adesso):
        print("Fuori orario (%s, solo giorni feriali %s): non pubblico." % (adesso.strftime("%a %H:%M"), ORE))
        return 0
    visti = set(stato.get("visti", []))
    nuovi = [l for l in lotti if l["id"] not in visti]
    if MODE in ("uno", "storia"):
        nuovi = sorted(lotti, key=lambda l: quando(l.get("pubblicata")) or datetime(2000, 1, 1), reverse=True)[:1]
    else:
        nuovi.sort(key=scadenza)
    fatti_oggi = stato.get("giorni", {}).get(oggi, 0)
    disponibili = max(0, MAX_DAY - fatti_oggi) if MODE == "send" else MAX_RUN
    da_fare = nuovi[: min(MAX_RUN, disponibili)]
    print("Beni nuovi: %d; oggi gia' pubblicati: %d; questa volta: %d." % (len(nuovi), fatti_oggi, len(da_fare)))
    if not da_fare and not (MODE == "send" and STORIE and stato.get("storie")):
        return 0
    USCITA.mkdir(exist_ok=True)
    token_ig = rinnova_token(stato) if MODE in ("uno", "send", "storia") else IG_TOKEN
    errori = 0
    nuove_storie = []
    for l in da_fare:
        l["titolo"] = titolo_completo(l)
        if MODE == "storia":
            pubblica_storie(l, token_ig)
            continue
        img = immagine_social.crea(l)
        percorso = USCITA / ("%s.jpg" % l["id"])
        img.save(percorso, quality=92)
        if MODE == "dry":
            immagine_social.crea_storia(l).save(USCITA / ("%s_storia.jpg" % l["id"]), quality=92)
            print("--- %s ---\nFacebook:\n%s\nInstagram:\n%s\n" % (l["id"], didascalia(l, False), didascalia(l, True)))
            continue
        try:
            foto_id, url_img = pubblica_facebook(percorso, didascalia(l, False))
        except Exception as e:  # noqa: BLE001
            print("ERRORE Facebook, bene %s: %s" % (l["id"], e), file=sys.stderr)
            errori += 1
            break  # nulla pubblicato: riproveremo alla prossima esecuzione
        print("Facebook ok: bene %s (foto %s)." % (l["id"], foto_id))
        visti.add(l["id"])  # su Facebook e' uscito: non lo ripubblichiamo mai piu'
        stato.setdefault("giorni", {})[oggi] = stato.get("giorni", {}).get(oggi, 0) + 1
        try:
            if not (token_ig and url_img):
                raise RuntimeError("manca il token Instagram o l'immagine")
            post = pubblica_instagram(url_img, didascalia(l, True), token_ig)
            print("Instagram ok: bene %s (post %s)." % (l["id"], post))
        except Exception as e:  # noqa: BLE001
            print("ERRORE Instagram, bene %s: %s" % (l["id"], e), file=sys.stderr)
            errori += 1
        if STORIE:
            nuove_storie.append({"id": l["id"], "quando": adesso.isoformat(timespec="seconds")})
    if MODE in ("uno", "send"):
        # Storie sfalsate: a ogni esecuzione esce UNA Storia, di un bene pubblicato in una esecuzione precedente
        attivi = {l["id"]: l for l in lotti}
        coda = []
        for it in stato.get("storie", []):
            try:
                ore = (adesso - datetime.fromisoformat(it["quando"])).total_seconds() / 3600
            except (KeyError, ValueError, TypeError):
                ore = 999
            if ore <= STORIA_ORE_MAX and it.get("id") in attivi:
                coda.append(it)
        if STORIE and coda:
            it = coda.pop(0)
            bene = attivi[it["id"]]
            bene["titolo"] = titolo_completo(bene)
            pubblica_storie(bene, token_ig)
        stato["storie"] = coda + nuove_storie
    if MODE in ("uno", "send", "storia"):
        stato["visti"] = sorted(visti)
        stato["giorni"] = {k: v for k, v in stato.get("giorni", {}).items() if k >= oggi[:8] + "01"}
        STATO.parent.mkdir(exist_ok=True)
        STATO.write_text(json.dumps(stato), encoding="utf-8")
    return 1 if errori else 0


if __name__ == "__main__":
    if MODE in ("uno", "send", "storia") and not (FB_TOKEN and FB_PAGE):
        print("Mancano FB_PAGE_TOKEN e FB_PAGE_ID")
        sys.exit(1)
    sys.exit(main())
