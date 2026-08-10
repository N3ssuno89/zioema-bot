#!/usr/bin/env python3
"""
ZioEMA — raccolta multi-sorgente.

Legge sources.json, trova gli articoli nuovi su N siti, e li filtra in due stadi
PRIMA di spendere token sulla scrittura del post.

    stadio 1  parole chiave      gratis, scarta l'80%: indoor, altri sport, ecc.
    stadio 2  triage col modello UNA chiamata per tutti i candidati insieme
    stadio 3  scrittura          solo per i sopravvissuti, una chiamata ciascuno

Aggiungere un sito = un blocco in sources.json. Nessuna riga di codice.

    python3 harvest.py --dry-run        stampa cosa passerebbe e perché
    python3 harvest.py                  genera e manda (via zioema_bot.send)
"""
import os, re, sys, json, time, html
from urllib.parse import urljoin, urlparse
import requests
from common import is_asset, expand_sitemap

UA = {"User-Agent": "Mozilla/5.0 (compatible; ZioEMA-bot/1.0; +https://instagram.com/zioema.official)",
      "Accept-Language": "it,en;q=0.8"}
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-4-6"
SEEN = "seen.json"

# --------------------------------------------------- stadio 1: parole chiave
# multilingua, minuscolo. Serve solo a NON pagare il modello per l'indoor.
# ATTENZIONE: polacco, ceco, tedesco declinano. "siatkówka plażowa" nel testo
# diventa "siatkówce plażowej", "siatkówki plażowej"... quindi si cercano RADICI,
# non parole intere. Le tuple = tutte le radici devono comparire.
BEACH = ["beach volley", "beachvolley", "beach-volley", "beachvolleyball",
         "vôlei de praia", "volei de praia", "vóley playa", "voley playa",
         "sandvolleyball", "strandröplabda", "plážov",
         ("siatków", "plaż"), ("siatkow", "plaz"), ("пляжн", "волейбол")]

# almeno uno di questi, oltre al beach, per essere internazionalmente rilevante
# Stadio 1 deve sbagliare per ECCESSO: meglio far passare rumore che perdere
# una notizia buona. La discriminazione fine la fa lo stadio 2 col modello.
INTL = ["beach pro tour", "bpt", "elite16", "elite 16", "challenge", "futures",
        "world championship", "world tour", "fivb", "cev", "olympic", "olimpi",
        "mistrzostw", "europejsk", "świat", "swiat", "igrzysk", "kontynental",
        "campionati europei", "campionato del mondo", "european champ",
        "italia", "italy", "włoch", "wloch", "italien", "azzurr", "italiensk"]


def get(url, tries=2):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 200:
                return r.text
            if r.status_code in (404, 403):
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (i + 1))
    return None


# ------------------------------------------------------------- scoperta URL
def find_feeds(home):
    """RSS/Atom: se il sito ce l'ha, è la strada giusta. Niente parser HTML da
    manutenere, niente rotture silenziose quando il sito cambia template."""
    out = []
    idx = get(home)
    if idx:
        out += [urljoin(home, u) for u in re.findall(
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)', idx)]
        out += [urljoin(home, u) for u in re.findall(
            r'href=["\']([^"\']*(?:/rss|/feed|\.rss|feed\.xml)[^"\']*)["\']', idx)][:3]
    for guess in ("/rss.xml", "/feed", "/rss", "/feed.xml", "/sitemap.xml"):
        out.append(urljoin(home, guess))
    return list(dict.fromkeys(out))


def urls_from_feed(xml, home):
    """Restituisce gli URL, i piu' recenti per primi.
    Un sitemap.xml non e' ordinato per data: se non si guarda <lastmod> si finisce
    per leggere venti articoli a caso invece degli ultimi venti."""
    if not xml or "<" not in xml:
        return []

    datati = []
    for blocco in re.findall(r"<url>(.*?)</url>", xml, flags=re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", blocco)
        mod = re.search(r"<lastmod>([^<]+)</lastmod>", blocco)
        if loc:
            datati.append((mod.group(1) if mod else "", loc.group(1).strip()))
    if any(d for d, _ in datati):
        datati.sort(reverse=True)
        return [urljoin(home, html.unescape(u)) for _, u in datati]

    links = re.findall(r"<link[^>]*>([^<]+)</link>", xml)
    links += re.findall(r'<link[^>]+href=["\']([^"\']+)', xml)
    links += re.findall(r"<loc>([^<]+)</loc>", xml)
    return [urljoin(home, html.unescape(u.strip())) for u in links]


def discover(src, limit=20):
    home = src["home"]
    pat = re.compile(src.get("url_pattern", ".")) if src.get("url_pattern") else None

    def keep(u):
        if not u.startswith(home):
            return False
        if is_asset(u):
            return False
        path = urlparse(u).path
        return bool(pat.search(path)) if pat else len(path) > 25

    # 1. feed dichiarati, poi autodiscovery
    for feed in (src.get("feeds") or []) + find_feeds(home):
        xml = get(feed)
        if xml and "sitemap" in feed and "<loc>" in xml:
            xml = expand_sitemap(xml, get, home)
        found = [u for u in urls_from_feed(xml, home) if keep(u)]
        if len(found) >= 3:
            return list(dict.fromkeys(found))[:limit], f"feed:{feed}"

    # 2. ripiego: le pagine elenco
    for listing in src.get("listings", []):
        page = get(listing)
        if not page:
            continue
        found = [urljoin(home, u) for u in re.findall(r'href=["\']([^"\']+)["\']', page)]
        found = [u for u in dict.fromkeys(found) if keep(u)]
        if len(found) >= 3:
            return found[:limit], f"html:{listing}"

    return [], "nessuna fonte leggibile"


# -------------------------------------------------- estrazione generica
def extract(url):
    page = get(url)
    if not page:
        return None

    def meta(prop):
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']'):
            m = re.search(pat, page, re.I)
            if m:
                return html.unescape(m.group(1))
        return None

    body = re.sub(r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>",
                  " ", page, flags=re.S | re.I)
    paras = [html.unescape(re.sub(r"<[^>]+>", "", p)) for p in
             re.findall(r"<p[^>]*>(.*?)</p>", body, flags=re.S)]
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    paras = [p for p in paras if len(p) > 70][:12]

    return {
        "url": url,
        "title": (meta("og:title") or meta("twitter:title") or "").split("|")[0].strip(),
        "photo": meta("og:image") or meta("twitter:image"),
        "body": "\n\n".join(paras),
        "text_lower": (page[:60000]).lower(),
    }


# ------------------------------------------------ dedup fra sorgenti diverse
STOP = set("""the a an of in on at to for and or with vs il lo la i gli le un una di
del della dei delle e ed a da in con su per tra fra che si non al alla ai alle dal
w i na z do o po za od the des der die das den und im am von zu op de het een en van
o os as do da dos das em com para e no na""".split())

def signature(title):
    """Token significativi del titolo: nomi propri, numeri, parole lunghe.
    Serve a riconoscere che 'Hamburg Elite16: Bryl/Losiak out' su fivb.com e su
    volleyballworld.com sono lo stesso fatto."""
    toks = re.findall(r"[0-9]+|[^\W\d_]{4,}", title.lower(), re.UNICODE)
    return {t for t in toks if t not in STOP}


def same_story(a, b, soglia=0.55):
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= soglia


def dedup(cands, sigs_pubblicate):
    """Tiene un solo articolo per fatto. A parita', vince la sorgente con
    'priorita' piu' bassa in sources.json (Volleyball World prima di FIVB, ecc.)."""
    cands = sorted(cands, key=lambda c: c["priorita"])
    tenuti, scarti = [], []
    for c in cands:
        sig = signature(c["title"])
        if any(same_story(sig, s) for s in sigs_pubblicate):
            scarti.append((c, "gia' pubblicato da un'altra fonte"))
            continue
        gemello = next((t for t in tenuti if same_story(sig, t["_sig"])), None)
        if gemello:
            scarti.append((c, f"stesso fatto di {gemello['fonte']}"))
            continue
        c["_sig"] = sig
        tenuti.append(c)
    return tenuti, scarti


def keyword_gate(art, src):
    """Stadio 1. Gratis. Se non nomina il beach volley, è indoor o altro sport."""
    t = art["text_lower"]
    def hit(k):
        return all(x in t for x in k) if isinstance(k, tuple) else k in t
    if not any(hit(k) for k in BEACH):
        return False, "non è beach volley"
    if src.get("sempre_rilevante"):
        return True, "sorgente sempre rilevante"
    if not any(hit(k) for k in INTL):
        return False, "beach ma nessun aggancio internazionale o italiano"
    return True, "candidato"


# ------------------------------------------------ stadio 2: triage col modello
TRIAGE = """Sei il caporedattore di ZioEMA, testata italiana di beach volley.
Ti passo una lista di articoli candidati. Per ognuno decidi se vale un post.

PUBBLICA se: coinvolge atleti o coppie italiane; è un torneo internazionale
(Beach Pro Tour, Elite16, Challenge, Futures, Europei, Mondiali, Olimpiadi);
cambia il quadro internazionale (calendario, regolamenti, infortuni di top player,
cambi di coppia ai vertici); oppure è il Campionato Italiano.

SCARTA se: è pallavolo indoor; è un campionato o torneo domestico di un altro Paese
senza italiani e senza top player internazionali; è un comunicato istituzionale
(nomine, bandi, sponsor, comunicati medici di atleti non italiani); è già
palesemente lo stesso fatto di un altro articolo della lista (tieni il migliore).

Nel dubbio scarta: un post irrilevante costa più di un post mancato.

Rispondi SOLO con JSON, un oggetto per articolo, nell'ordine ricevuto:
[{"i":0,"pubblica":true,"perche":"..."}, ...]"""


def triage(cands):
    listing = "\n\n".join(
        f'[{i}] FONTE: {c["fonte"]}\nTITOLO: {c["title"]}\nAPERTURA: {c["body"][:350]}'
        for i, c in enumerate(cands))
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 2000, "system": TRIAGE,
              "messages": [{"role": "user", "content": listing}]}, timeout=120)
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json()["content"])
    return json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())


# ------------------------------------------------------------------- main
def preflight():
    """Verifica che il repo sia completo. Non serve nessun secret."""
    import importlib
    ok = True
    attesi = ["zioema_bot.py", "zioema_post.py", "sources.json", "seen.json",
              "fonts/Anton-Regular.ttf", "fonts/Archivo-Bold.ttf"]
    for f in attesi:
        e = os.path.exists(f)
        print(f"  {'OK ' if e else 'MANCA'}  {f}")
        ok &= e
    print(f"  {'OK ' if os.path.exists('zioema.png') else 'assente'}  zioema.png (senza, cerchio grigio)")
    try:
        srcs = json.load(open("sources.json"))
        print(f"  OK    sources.json: {len(srcs)} siti")
    except Exception as e:
        print(f"  MANCA sources.json illeggibile: {e}"); ok = False
    for mod in ("PIL", "requests", "cv2"):
        try:
            importlib.import_module(mod); print(f"  OK    libreria {mod}")
        except ImportError:
            print(f"  MANCA libreria {mod}"); ok = False
    for k in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        print(f"  {'OK   ' if os.environ.get(k) else 'assente'}  secret {k}")
    print("\n>>> REPO COMPLETO" if ok else "\n>>> MANCA QUALCOSA, vedi sopra")
    return ok


def main():
    dry = "--dry-run" in sys.argv
    sources = json.load(open("sources.json"))
    try:
        seen = set(json.load(open(SEEN)))
    except Exception:
        seen = set()

    giro = int(time.time() // 900)          # un giro ogni 15 minuti
    cands, scartati = [], []
    for src in sources:
        if src.get("attivo") is False:
            continue
        tier = src.get("tier", 3)
        # tier1 ogni giro, tier2 ogni ora, tier3 due volte al giorno
        if tier == 2 and giro % 4 != 0:
            continue
        if tier == 3 and giro % 48 != 0:
            continue
        urls, how = discover(src)
        print(f"[{src['id']}] {how} -> {len(urls)} link")
        for u in urls:
            if u in seen:
                continue
            art = extract(u)
            if not art or not art["title"] or not art["photo"] or len(art["body"]) < 200:
                scartati.append((u, "articolo incompleto"))
                seen.add(u)
                continue
            ok, why = keyword_gate(art, src)
            art.update(fonte=src["fonte"], lang=src["lang"], src=src["id"],
                       tag=src["tag"], priorita=src.get("priorita", 99))
            (cands if ok else scartati).append(art if ok else (u, why))
            if not ok:
                seen.add(u)
            time.sleep(2)

    print(f"\nstadio 1: {len(cands)} candidati, {len(scartati)} scartati sulle parole chiave")

    try:
        sigs = [set(x) for x in json.load(open("sigs.json"))]
    except Exception:
        sigs = []
    cands, doppioni = dedup(cands, sigs)
    for c, why in doppioni:
        print(f"  doppione  [{c['fonte']}] {c['title'][:60]} — {why}")
    print(f"dedup: {len(cands)} rimasti, {len(doppioni)} doppioni")

    if not cands:
        json.dump(sorted(seen)[-2000:], open(SEEN, "w"), indent=1)
        return

    if not API_KEY:
        print("\n(nessuna ANTHROPIC_API_KEY: mi fermo qui, lo stadio 2 non parte)")
        return
    verdetti = triage(cands)
    promossi = []
    for v in verdetti:
        c = cands[v["i"]]
        mark = "PUBBLICA" if v["pubblica"] else "scarta  "
        print(f"  {mark}  [{c['fonte']}] {c['title'][:70]}  — {v['perche'][:60]}")
        (promossi if v["pubblica"] else []).append(c) if v["pubblica"] else seen.add(c["url"])

    print(f"\nstadio 2: {len(promossi)} da scrivere su {len(cands)} candidati")
    if dry:
        print("(dry-run: non genero e non mando niente)")
        return

    import zioema_bot
    for c in promossi:
        try:
            print(f"> {c['url']}")
            zioema_bot.process(c["url"])
            seen.add(c["url"])
            sigs.append(c["_sig"])
        except Exception as e:
            print(f"  ! {e}")
    json.dump(sorted(seen)[-2000:], open(SEEN, "w"), indent=1)
    json.dump([sorted(s_) for s_ in sigs[-300:]], open("sigs.json", "w"))


if __name__ == "__main__":
    if "--check" in sys.argv:
        print("CONTROLLO REPO\n")
        sys.exit(0 if preflight() else 1)
    if not API_KEY and "--dry-run" not in sys.argv:
        sys.exit("manca ANTHROPIC_API_KEY")
    main()
