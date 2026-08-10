#!/usr/bin/env python3
"""
ZioEMA — diagnostica sorgenti. NON serve la chiave API, non costa niente.

    python3 check_sources.py

Per ogni sito prova, in ordine: RSS/Atom -> sitemap -> pagine elenco.
Stampa quanti link articolo trova e i primi tre, così si vede se il
'url_pattern' in sources.json è giusto o va corretto.

Mandami l'output intero: e' l'unica cosa che mi manca per chiudere la configurazione.
"""
import json, re, sys, time
from urllib.parse import urljoin, urlparse
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; ZioEMA-bot/1.0; +https://instagram.com/zioema.official)"}


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        return r.status_code, r.text if r.status_code == 200 else ""
    except requests.RequestException as e:
        return type(e).__name__, ""


def feeds_for(home, page):
    out = []
    if page:
        out += [urljoin(home, u) for u in re.findall(
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', page)]
    out += [urljoin(home, g) for g in ("/rss.xml", "/feed", "/rss", "/feed.xml", "/sitemap.xml")]
    return list(dict.fromkeys(out))


def links_from(text, home):
    urls = re.findall(r"<loc>([^<]+)</loc>", text)
    urls += re.findall(r"<link[^>]*>([^<]+)</link>", text)
    urls += re.findall(r'href=["\']([^"\']+)["\']', text)
    return [urljoin(home, u.strip()) for u in urls]


def check(src):
    home = src["home"]
    pat = re.compile(src.get("url_pattern", "."))
    keep = lambda u: u.startswith(home) and bool(pat.search(urlparse(u).path))

    code, page = get(home)
    print(f"\n=== [{src['id']}] {home}   (tier {src.get('tier','?')})")
    print(f"    home: HTTP {code}")
    if code != 200:
        print("    ! irraggiungibile o blocca il bot -> da valutare se tenerlo")
        return

    for feed in feeds_for(home, page):
        c, txt = get(feed)
        if c != 200 or "<" not in txt:
            continue
        hits = [u for u in dict.fromkeys(links_from(txt, home)) if keep(u)]
        tot = len(dict.fromkeys(links_from(txt, home)))
        if hits:
            print(f"    FEED OK  {feed}  -> {len(hits)}/{tot} link passano il pattern")
            for u in hits[:3]:
                print(f"             {u}")
            return
        elif tot > 5:
            print(f"    feed trovato ma pattern SBAGLIATO: {feed} ({tot} link, 0 passano)")
            for u in list(dict.fromkeys(links_from(txt, home)))[:3]:
                print(f"             esempio: {urlparse(u).path}")
            return

    for listing in src.get("listings", []):
        c, txt = get(listing)
        if c != 200:
            print(f"    elenco {listing}: HTTP {c}")
            continue
        allu = list(dict.fromkeys(links_from(txt, home)))
        hits = [u for u in allu if keep(u)]
        if hits:
            print(f"    HTML OK  {listing}  -> {len(hits)}/{len(allu)} link passano il pattern")
            for u in hits[:3]:
                print(f"             {u}")
            return
        print(f"    elenco {listing}: {len(allu)} link, 0 passano il pattern")
        for u in allu[:5]:
            print(f"             esempio: {urlparse(u).path}")
        return

    print("    ! niente da nessuna parte")


if __name__ == "__main__":
    srcs = json.load(open("sources.json"))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for s in srcs:
        if only and s["id"] != only:
            continue
        check(s)
        time.sleep(1.5)
    print("\nFatto. Copia tutto l'output e mandamelo.")
