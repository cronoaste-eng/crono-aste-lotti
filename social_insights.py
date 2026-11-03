#!/usr/bin/env python3
"""Statistiche Facebook/Instagram in sola lettura. Solo ASCII. Non stampa mai i token.

SOCIAL_INSIGHTS_MODE=permessi : prova permessi e metriche disponibili (nessuna scrittura)
SOCIAL_INSIGHTS_MODE=prova    : raccoglie gli ultimi 28 giorni e stampa i totali (nessuna scrittura)
SOCIAL_INSIGHTS_MODE=raccogli : come prova, e scrive data/social_insights.json (solo se i dati sono completi)

Solo conteggi aggregati per giorno, mai dati di persone. I giorni sono quelli di Meta (fuso Pacifico), non Europe/Rome.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

MODE = (os.environ.get("SOCIAL_INSIGHTS_MODE") or "permessi").strip().lower()
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN", "")
FB_PAGE = os.environ.get("FB_PAGE_ID", "")
IG_TOKEN = os.environ.get("INSTAGRAM_TOKEN", "")
IG_USER = os.environ.get("IG_USER_ID") or "17841445610038092"
GRAPH_FB = "https://graph.facebook.com/v23.0"
GRAPH_IG = "https://graph.instagram.com/v23.0"


def mask(s):
    s = str(s)
    for t in (FB_TOKEN, IG_TOKEN):
        if t:
            s = s.replace(t, "***")
    return s


def chiama(url, params, token):
    p = dict(params)
    p["access_token"] = token
    try:
        r = requests.get(url, params=p, timeout=40)
        try:
            j = r.json()
        except ValueError:
            return r.status_code, {"error": {"message": r.text[:200]}}
        return r.status_code, j
    except Exception as e:  # noqa: BLE001
        return 0, {"error": {"message": mask(e)}}


def esito(code, j):
    if code == 200 and "error" not in j:
        return "OK"
    e = j.get("error", {})
    return "ERRORE %s: %s" % (e.get("code"), mask(e.get("message", ""))[:160])


def permessi():
    oggi = datetime.now(ZoneInfo("Europe/Rome")).date()
    ieri, tre = oggi - timedelta(days=1), oggi - timedelta(days=4)
    print("== Facebook: debug_token (token pagina) ==")
    c, j = chiama(GRAPH_FB + "/debug_token", {"input_token": FB_TOKEN}, FB_TOKEN)
    d = j.get("data", {}) if c == 200 else {}
    if d:
        print("tipo:", d.get("type"), "| valido:", d.get("is_valid"), "| scadenza:", d.get("expires_at"),
              "| accesso dati scade:", d.get("data_access_expires_at"))
        print("permessi:", ", ".join(d.get("scopes", [])))
    else:
        print(esito(c, j))
    print("\n== Facebook: metriche Pagina (periodo day, ultimi 3 giorni) ==")
    for m in ["page_media_view", "page_total_media_view_unique", "page_views_total", "page_post_engagements", "page_follows",
              "page_daily_follows", "page_daily_follows_unique", "page_fan_adds", "page_impressions", "page_impressions_unique",
              "page_engaged_users", "page_total_actions", "page_website_clicks_logged_in_unique", "page_consumptions",
              "page_actions_post_reactions_total", "page_cta_clicks_logged_in_total"]:
        c, j = chiama("%s/%s/insights" % (GRAPH_FB, FB_PAGE),
                      {"metric": m, "period": "day", "since": tre.isoformat(), "until": oggi.isoformat()}, FB_TOKEN)
        val = ""
        if c == 200 and j.get("data"):
            val = " valori=" + ",".join(str(v.get("value")) for v in j["data"][0].get("values", []))[:80]
        print("  %-42s %s%s" % (m, esito(c, j), val))
    print("\n== Facebook: campi Pagina ==")
    c, j = chiama("%s/%s" % (GRAPH_FB, FB_PAGE), {"fields": "name,followers_count,fan_count"}, FB_TOKEN)
    print(" ", esito(c, j), json.dumps(j) if c == 200 else "")
    print("\n== Instagram: account e metriche (total_value, un giorno) ==")
    c, j = chiama("%s/%s" % (GRAPH_IG, IG_USER), {"fields": "username,followers_count,media_count"}, IG_TOKEN)
    print(" ", esito(c, j), json.dumps(j) if c == 200 else "")
    for m in ["views", "reach", "accounts_engaged", "total_interactions", "likes", "comments", "shares", "saves", "replies",
              "profile_links_taps", "website_clicks", "profile_views", "follows_and_unfollows", "reposts"]:
        c, j = chiama("%s/%s/insights" % (GRAPH_IG, IG_USER),
                      {"metric": m, "metric_type": "total_value", "period": "day", "since": tre.isoformat(), "until": ieri.isoformat()}, IG_TOKEN)
        val = ""
        if c == 200 and j.get("data"):
            val = " totale=%s" % j["data"][0].get("total_value", {}).get("value")
        print("  %-26s %s%s" % (m, esito(c, j), val))
    c, j = chiama("%s/%s/insights" % (GRAPH_IG, IG_USER), {"metric": "follower_count", "period": "day", "since": tre.isoformat(), "until": ieri.isoformat()}, IG_TOKEN)
    print("  %-26s %s" % ("follower_count (serie)", esito(c, j)))
    return 0


GIORNI = 28
FB_METRICHE = {"visualizzazioni": "page_media_view", "interazioni": "page_post_engagements", "clic": "page_total_actions",
               "visualizzazioni_profilo": "page_views_total", "nuovi_follower": "page_daily_follows"}
FB_EXTRA = ["page_total_media_view_unique", "page_actions_post_reactions_total", "page_follows"]
IG_METRICHE = {"visualizzazioni": "views", "interazioni": "total_interactions", "clic": "profile_links_taps",
               "visualizzazioni_profilo": "profile_views"}


def vuoto():
    return {k: None for k in list(FB_METRICHE)}


def somma_valore(v):
    if isinstance(v, dict):  # alcune metriche danno un dettaglio (es. reazioni per tipo)
        return sum(x for x in v.values() if isinstance(x, (int, float)))
    return v


def facebook(giorni, extra=False):
    """{giorno: {metrica: valore}} dalla Pagina. Il giorno e' quello che finisce a end_time (data di Meta, fuso Pacifico)."""
    oggi = datetime.now(ZoneInfo("Europe/Rome")).date()
    out, errori = {g: vuoto() for g in giorni}, 0
    elenco = dict(FB_METRICHE)
    if extra:
        elenco.update({m: m for m in FB_EXTRA})
    serie = {}
    for nome, m in elenco.items():
        c, j = chiama("%s/%s/insights" % (GRAPH_FB, FB_PAGE),
                      {"metric": m, "period": "day", "since": (oggi - timedelta(days=GIORNI + 2)).isoformat(),
                       "until": (oggi + timedelta(days=1)).isoformat()}, FB_TOKEN)
        if c != 200 or not j.get("data"):
            print("  Facebook %s: %s" % (m, esito(c, j)), file=sys.stderr)
            errori += 1
            continue
        for v in j["data"][0].get("values", []):
            fine = datetime.fromisoformat(v["end_time"].replace("Z", "+00:00").replace("+0000", "+00:00"))
            g = (fine - timedelta(hours=12)).date().isoformat()
            serie.setdefault(nome, {})[g] = somma_valore(v.get("value"))
    for nome, s_ in serie.items():
        for g in giorni:
            if nome in FB_METRICHE:
                out[g][nome] = s_.get(g)
    return out, errori, serie


def instagram(giorni):
    out, errori = {g: vuoto() for g in giorni}, 0
    for g in giorni:
        d = datetime.fromisoformat(g).date()
        c, j = chiama("%s/%s/insights" % (GRAPH_IG, IG_USER),
                      {"metric": ",".join(IG_METRICHE.values()), "metric_type": "total_value", "period": "day",
                       "since": g, "until": (d + timedelta(days=1)).isoformat()}, IG_TOKEN)
        if c != 200 or not j.get("data"):
            print("  Instagram %s: %s" % (g, esito(c, j)), file=sys.stderr)
            errori += 1
            continue
        per_metrica = {x["name"]: x.get("total_value", {}).get("value") for x in j["data"]}
        for nome, m in IG_METRICHE.items():
            out[g][nome] = per_metrica.get(m)
    c, j = chiama("%s/%s/insights" % (GRAPH_IG, IG_USER),
                  {"metric": "follower_count", "period": "day", "since": giorni[0], "until": (datetime.fromisoformat(giorni[-1]).date() + timedelta(days=1)).isoformat()}, IG_TOKEN)
    if c == 200 and j.get("data"):
        for v in j["data"][0].get("values", []):
            g = v["end_time"][:10]
            # end_time e' l'inizio del giorno dopo quello misurato
            g = (datetime.fromisoformat(g).date() - timedelta(days=1)).isoformat()
            if g in out:
                out[g]["nuovi_follower"] = v.get("value")
    else:
        print("  Instagram follower_count: %s" % esito(c, j), file=sys.stderr)
        errori += 1
    return out, errori


def raccogli(scrivi):
    oggi = datetime.now(ZoneInfo("Europe/Rome"))
    giorni = [(oggi.date() - timedelta(days=i)).isoformat() for i in range(GIORNI, 0, -1)]
    fb, e1, serie = facebook(giorni, extra=not scrivi)
    ig, e2 = instagram(giorni)
    doc = {"aggiornato": oggi.isoformat(timespec="seconds"),
           "giorni": [{"giorno": g, "facebook": fb[g], "instagram": ig[g]} for g in giorni]}
    print("Totali %d giorni (%s - %s):" % (GIORNI, giorni[0], giorni[-1]))
    for piat, dati in (("facebook", fb), ("instagram", ig)):
        tot = {k: sum((dati[g][k] or 0) for g in giorni) for k in vuoto()}
        vuoti = sum(1 for g in giorni for k in vuoto() if dati[g][k] is None)
        print("  %-10s %s  (valori mancanti: %d)" % (piat, json.dumps(tot), vuoti))
    if not scrivi:
        for nome, s_ in serie.items():
            if nome in FB_EXTRA:
                print("  extra FB %s somma %d giorni: %s" % (nome, GIORNI, sum((s_.get(g) or 0) for g in giorni)))
        print("Ultimi 3 giorni:", json.dumps(doc["giorni"][-3:]))
        return 0
    if e1 or e2:
        print("Errori nelle chiamate (%d): file NON aggiornato." % (e1 + e2), file=sys.stderr)
        return 1
    p = os.path.join("data", "social_insights.json")
    os.makedirs("data", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("Scritto", p)
    return 0


if __name__ == "__main__":
    if MODE == "permessi":
        sys.exit(permessi())
    if MODE == "debugfb":
        for m, per in [("page_media_view", "day"), ("page_media_view", "days_28"), ("page_views_total", "day"), ("page_follows", "day")]:
            c, j = chiama("%s/%s/insights" % (GRAPH_FB, FB_PAGE), {"metric": m, "period": per}, FB_TOKEN)
            print(m, per, c, mask(json.dumps(j))[:600])
        c, j = chiama("%s/%s/insights" % (GRAPH_FB, FB_PAGE), {"metric": "page_media_view", "period": "day", "date_preset": "last_7d"}, FB_TOKEN)
        print("date_preset", c, mask(json.dumps(j))[:600])
        sys.exit(0)
    if MODE in ("prova", "raccogli"):
        sys.exit(raccogli(MODE == "raccogli"))
    print("Modalita' sconosciuta:", MODE)
    sys.exit(1)
