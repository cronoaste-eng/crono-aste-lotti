#!/usr/bin/env python3
"""Statistiche Facebook/Instagram in sola lettura. Solo ASCII. Non stampa mai i token.

SOCIAL_INSIGHTS_MODE=permessi : prova permessi e metriche disponibili (nessuna scrittura)
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


if __name__ == "__main__":
    if MODE == "permessi":
        sys.exit(permessi())
    print("Modalita' sconosciuta:", MODE)
    sys.exit(1)
