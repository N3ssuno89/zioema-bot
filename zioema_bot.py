#!/usr/bin/env python3
"""
ZioEMA bot — sorveglia federvolley.it, genera il post, lo manda su Telegram.

NON PUBBLICA SU INSTAGRAM. Ti arriva la bozza, la carichi tu.

VARIABILI D'AMBIENTE RICHIESTE
    ANTHROPIC_API_KEY
    TELEGRAM_BOT_TOKEN     da @BotFather
    TELEGRAM_CHAT_ID       il tuo id (scrivi al bot, poi apri
                           https://api.telegram.org/bot<TOKEN>/getUpdates)

USO
    python3 zioema_bot.py                 # ciclo normale: cerca novità e le manda
    python3 zioema_bot.py --dry-run       # genera ma non manda niente
    python3 zioema_bot.py <url>           # forza un articolo specifico

STATO
    seen.json — url già processati. Se lo cancelli, rimanda tutto.
"""
import os, re, sys, json, time, subprocess
import requests

# UNA SOLA lista di sorgenti, condivisa con harvest.py: sources.json.
# Prima qui ce n'era una seconda, hardcoded, e le sorgenti non previste
# ripiegavano su federvolley -> fonte sbagliata stampata sulla grafica.
def _carica_sorgenti():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")) as f:
        srcs = json.load(f)
    for x in srcs:
        x["base"] = x["home"]
        x.setdefault("beach_re", r"beach\s*volley|siatk\w*\s+pla\w*|v[oô]lei de praia|beachvolley")
        intl = x.get("tag") != "IT"
        x["tag"] = "🌍 INTERNAZIONALE" if intl else "🇮🇹 ITALIA"
        x["token_env"]  = "TG_TOKEN_INTL"  if intl else "TG_TOKEN_ITALIA"
        x["chat_env"]   = "TG_CHAT_INTL"   if intl else "TG_CHAT_ITALIA"
        x["thread_env"] = "TG_THREAD_INTL" if intl else "TG_THREAD_ITALIA"
    return srcs

SOURCES = _carica_sorgenti()
BASE      = SOURCES[0]["base"]
SEEN_FILE = "seen.json"
DRAFTS    = "drafts"
MODEL     = "claude-sonnet-4-6"
UA        = {"User-Agent": "ZioEMA-bot/1.0 (+https://instagram.com/zioema.official)"}
DRY       = "--dry-run" in sys.argv

API_KEY   = os.environ.get("ANTHROPIC_API_KEY")
TG_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT   = os.environ.get("TELEGRAM_CHAT_ID")

STOP_MARKERS = ["FORMULA DEL TORNEO", "DIRETTA STREAMING", "HIGHLIGHTS E FOTOGALLERY",
                "Articoli correlati", "PROGRAMMA", "IL TABELLONE", "ALBO D'ORO"]

# elenchi da cui pescare i link, in ordine di preferenza.
# il sito è Next.js: alcune liste sono renderizzate lato client e con requests
# non si vedono. discover() prova in ordine e usa la prima che restituisce link.
LISTINGS = [
    f"{BASE}/categorie-news/news-beach-volley",
    f"{BASE}/campionati/beach-volley/news",
    f"{BASE}/news?categoria=Beach%20Volley",
    f"{BASE}/",
]
SITEMAPS = [f"{BASE}/sitemap.xml", f"{BASE}/sitemap_index.xml"]

ART_RE = re.compile(r'href="(?:' + re.escape(BASE) + r')?(/[a-z0-9][a-z0-9\-]{24,})"')
SKIP   = ("/campionati", "/nazionali", "/categorie-news", "/news", "/archivio",
          "/regolamenti", "/trasparenza", "/comitati", "/assemblea", "/stagione")


# ------------------------------------------------------------------ HTTP
def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


# -------------------------------------------------------------- DISCOVERY
def src_for(url):
    cand = [x for x in SOURCES if url.startswith(x["base"])]
    if not cand:
        raise ValueError(f"sorgente sconosciuta per {url}: aggiungila a sources.json")
    return max(cand, key=lambda x: len(x["base"]))


def discover(limit=15):
    """Restituisce URL candidati. Non filtra per categoria: quella si legge
    sull'articolo, che è l'unico posto dove è garantita."""
    out = []
    for s_ in SOURCES:
        for listing in s_["listings"]:
            html = get(listing)
            if not html:
                continue
            found = []
            for path in re.findall(s_["art_re"], html):
                if any(path.startswith(k) for k in s_["skip"]):
                    continue
                u = s_["base"] + path if path.startswith("/") else path
                if u not in found:
                    found.append(u)
            if len(found) >= 3:
                print(f"  [{s_['id']}] {listing}: {len(found)} link")
                out += found[:limit]
                break
        else:
            print(f"  [{s_['id']}] nessun elenco leggibile")
    if out:
        return out

    for sm in SITEMAPS:                       # fallback
        xml = get(sm)
        if xml and "<loc>" in xml:
            locs = re.findall(r"<loc>([^<]+)</loc>", xml)
            arts = [u for u in locs if u.startswith(BASE)
                    and not any(u.replace(BASE, "").startswith(s) for s in SKIP)]
            if arts:
                print(f"  elenco: {sm} (sitemap, {len(arts)} link)")
                return arts[-limit:][::-1]

    print("  ! nessun elenco leggibile — vedi README, sezione 'se discover non trova nulla'")
    return []


# ----------------------------------------------------------------- PARSING
def parse_article(url, src=None):
    src = src or src_for(url)
    html = get(url)
    if not html:
        return None

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)', html)
        if not m:
            m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', html)
        return m.group(1) if m else None

    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", body)

    # LA CATEGORIA E' STAMPATA SULLA PAGINA. Niente classificazione via LLM.
    is_beach = bool(re.search(src["beach_re"], text, re.I))

    paras = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, flags=re.S)]
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    paras = [p for p in paras if len(p) > 60]
    clean = []
    for p in paras:
        if any(m.upper() in p.upper()[:45] for m in STOP_MARKERS):
            break
        clean.append(p)

    related = [src["base"] + r if r.startswith("/") else r
               for r in dict.fromkeys(re.findall(src["art_re"], body))]
    related = [r for r in related if r != url][:2]

    m = re.search(r"(\d{2}\s+[a-z]+\s+20\d{2})", text)
    return {"url": url, "src": src["id"], "fonte": src["fonte"], "lang": src["lang"],
            "title": (meta("og:title") or "").split("|")[0].strip(),
            "date": m.group(1) if m else None, "photo": meta("og:image"),
            "body": "\n\n".join(clean), "related": related, "is_beach": is_beach}


# -------------------------------------------------------------- COPYWRITING
SYSTEM = """Sei il caporedattore di ZioEMA, testata italiana di beach volley su Instagram.
Ricevi un articolo di Federvolley e produci titolo grafica + caption.

REGOLE TITOLO (vincolanti)
- Esattamente 2 righe, MAIUSCOLO, senza virgolette.
- 14-17 caratteri per riga. Sotto i 14 la riga resta mezza vuota, sopra i 17 il font rimpicciolisce.
- Clickbait SI, fuorviante NO: il post deve mantenere quello che il titolo promette.
- Vietate le parole ambigue senza contesto che le disambigui. "KO" in italiano vuol dire
  sia "sconfitto" sia "infortunato": usalo solo se la riga accanto chiarisce quale dei due.
- Non chiamare "impresa" o "sorpresa" una vittoria di chi era favorito: controlla seeding
  o ranking nell'articolo prima di scegliere il registro.
- Il gancio migliore è quasi sempre un numero o un nome grosso, non l'esito generico.

REGOLE CAPTION
- Payoff nei PRIMI 125 CARATTERI: Instagram tronca lì e il titolo ha già promesso qualcosa.
- Punteggi e tempi come gancio narrativo, non come elenco.
- Il gancio in avanti (prossimo match: data, ora, posta in gioco) va in ALTO, non in fondo.
  Se orario o avversario non sono noti non prometterli: di' che arrivano in Story.
- Chiudi con "📌 Fonte: {FONTE}" (te la passo io) e 6-8 hashtag pertinenti.
- Italiano, tono diretto, emoji con parsimonia.

VALORE AGGIUNTO — la parte che conta
Ti passo anche gli articoli precedenti collegati. Cerca dati che Federvolley HA ma NON HA
COLLEGATO: serie aperte, set persi/vinti nel torneo, imbattibilità, precedenti, titoli in
carica citati di sfuggita. Se trovi una statistica del genere, è il titolo. Spiega in
"insight" come l'hai ricavata, così la verifico.

FILTRO DI RILEVANZA — decidi PRIMA di scrivere
ZioEMA parla a un pubblico italiano di beach volley. Metti "skip": true e spiega in
"skip_reason" se la notizia è: pallavolo indoor; un torneo domestico di un altro Paese
senza italiani; una comunicazione istituzionale (nomine, bandi, comunicati medici di
atleti non italiani). Metti "skip": false solo se c'è ALMENO UNA di queste:
- atleti o coppie italiane coinvolte
- un torneo internazionale a cui l'Italia partecipa (BPT, Europei, Mondiali, Olimpiadi)
- una notizia che cambia il quadro internazionale del beach (calendario, regolamenti,
  infortuni di big, cambi di coppia ai vertici)
Nel dubbio: skip. Un post irrilevante costa più di un post mancato.

SE L'ARTICOLO NON È IN ITALIANO
- Traduci il senso, non le parole. Il risultato deve leggersi come scritto in italiano.
- NOMI PROPRI SEMPRE AL NOMINATIVO. Il polacco declina: "Bryla", "Łosiaka", "Kantora"
  nel testo sono i casi obliqui di Bryl, Łosiak, Kantor. Non trascriverli mai declinati.
  Stessa cosa per i luoghi: "w Starych Jabłonkach" -> "a Stare Jablonki".
- Mantieni i segni diacritici nei cognomi (Łosiak, Wojtasik). I font li supportano.
- Abbreviazioni polacche: ME = Campionati Europei, MŚ = Mondiali, MP = Campionati
  polacchi, IO = Olimpiadi, PP = Coppa di Polonia.
- Glossario: siatkówka plażowa = beach volley; mecz = partita; set = set;
  turniej = torneo; faza grupowa = fase a gironi; ćwierćfinał = quarti;
  półfinał = semifinale; awans = qualificazione; porażka = sconfitta;
  zwycięstwo = vittoria; kontuzja = infortunio.
- Se un nome o un punteggio è ambiguo dopo la traduzione, mettilo in "warnings"
  invece di indovinare.

Se un dato non è nell'articolo NON inventarlo: mettilo in "warnings".

Rispondi SOLO con JSON, niente markdown, niente backtick:
{"skip":false,"skip_reason":"","line1":"...","line2":"...","caption":"...","insight":"...","warnings":["..."]}"""


def generate_copy(article, context):
    ctx = "\n\n".join(f"--- ARTICOLO PRECEDENTE COLLEGATO ---\n{c['title']}\n{c['body']}"
                      for c in context if c)
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 1500, "system": SYSTEM,
              "messages": [{"role": "user", "content":
                  f"FONTE: {article['fonte']} (lingua: {article['lang']})\n"
                  f"TITOLO ORIGINALE: {article['title']}\n"
                  f"Data: {article['date']}\n\n{article['body']}\n\n{ctx}"}]},
        timeout=90)
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json()["content"])
    return json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())


# ------------------------------------------------------------ CROP AUTOMATICO
def smart_crop(path):
    import cv2
    img = cv2.imread(path)
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cc.detectMultiScale(gray, 1.08, 6, minSize=(int(h * .05), int(h * .05)))

    if len(faces) == 0:
        return {"CROP_TOP": 0, "CROP_BOTTOM": int(h * .80), "CX": w // 2,
                "PHOTO_H": 900, "FADE_START": 520,
                "warning": "NESSUN VOLTO RILEVATO — la foto non rispetta il criterio editoriale, cambiala"}

    fh_max = max(f[3] for f in faces)
    warn = "VOLTI PICCOLI — l'emozione non si legge, valuta un'altra foto" if fh_max < h * .12 else None
    cx = int(sum(f[0] + f[2] / 2 for f in faces) / len(faces))
    top = max(0, int(min(f[1] for f in faces) - fh_max * .9))
    bottom = min(h, int(max(f[1] + f[3] for f in faces) + fh_max * 4.5))   # stima linea vita
    if bottom - top < h * .35:
        bottom = min(h, top + int(h * .55))
    return {"CROP_TOP": top, "CROP_BOTTOM": bottom, "CX": cx,
            "PHOTO_H": 900, "FADE_START": 520, "warning": warn}


# ------------------------------------------------------------------ TELEGRAM
def tg(method, chat=None, thread=None, token=None, **kw):
    files = kw.pop("files", None)
    data = {"chat_id": chat or TG_CHAT, **kw}
    if thread:
        data["message_thread_id"] = thread
    r = requests.post(f"https://api.telegram.org/bot{token or TG_TOKEN}/{method}",
                      data=data, files=files, timeout=60)
    if not r.ok:
        print(f"  ! telegram {method}: {r.text[:200]}")
    return r.ok


def send(post_path, copy, article, warnings):
    # stesso bot, destinazioni diverse: una chat (o un topic) per filone editoriale
    src = next(s_ for s_ in SOURCES if s_["id"] == article["src"])
    route = {
        "token":  os.environ.get(src.get("token_env", ""), "")  or TG_TOKEN,
        "chat":   os.environ.get(src.get("chat_env", ""), "")   or TG_CHAT,
        "thread": os.environ.get(src.get("thread_env", ""), "") or None,
    }

    # 3 messaggi separati: la caption da sola si copia con un tap sul telefono.
    with open(post_path, "rb") as f:
        tg("sendPhoto", files={"photo": f}, **route,
           caption=f"{src['tag']}  ·  {copy['line1']} {copy['line2']}")
    tg("sendMessage", text=copy["caption"], disable_web_page_preview=True, **route)
    tail = [f"🔗 {article['url']}"]
    if copy.get("insight"):
        tail.append(f"💡 {copy['insight']}")
    for w in warnings:
        tail.append(f"⚠️ {w}")
    tg("sendMessage", text="\n\n".join(tail), disable_web_page_preview=True, **route)


# ---------------------------------------------------------------------- MAIN
def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()


def process(url):
    art = parse_article(url)
    if not art or not art["title"]:
        return "illeggibile"
    if not art["is_beach"]:
        return "non beach"
    if not art["photo"] or len(art["body"]) < 200:
        return "senza foto o troppo corto"

    slug = re.sub(r"[^a-z0-9]+", "-", art["title"].lower())[:60].strip("-")
    out = os.path.join(DRAFTS, slug)
    os.makedirs(out, exist_ok=True)
    photo = os.path.join(out, "_photo.jpg")
    open(photo, "wb").write(requests.get(art["photo"], headers=UA, timeout=40).content)

    context = [parse_article(r) for r in art["related"]]
    copy = generate_copy(art, context)
    if copy.get("skip"):
        return f"scartata: {copy.get('skip_reason','non rilevante')}"
    crop = smart_crop(photo)
    post = os.path.join(out, "post.jpg")

    env = {**os.environ, "SRC": photo, "FONTE": art["fonte"],
           **{k: str(v) for k, v in crop.items() if k != "warning"}}
    subprocess.run([sys.executable, "zioema_post.py", post, copy["line1"], copy["line2"]],
                   env=env, check=True)

    warnings = copy.get("warnings", []) + ([crop["warning"]] if crop["warning"] else [])
    json.dump({**art, "copy": copy, "crop": crop}, open(os.path.join(out, "meta.json"), "w"),
              ensure_ascii=False, indent=2)
    open(os.path.join(out, "caption.txt"), "w").write(copy["caption"])

    if DRY:
        print(f"  [dry-run] {copy['line1']} / {copy['line2']}")
    else:
        send(post, copy, art, warnings)
    return "OK"


if __name__ == "__main__":
    if not API_KEY:
        sys.exit("manca ANTHROPIC_API_KEY")
    if not DRY and not (TG_TOKEN and TG_CHAT):
        sys.exit("mancano TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")

    forced = [a for a in sys.argv[1:] if a.startswith("http")]
    seen = load_seen()
    targets = forced or [u for u in discover() if u not in seen]
    print(f"{len(targets)} da valutare")

    for u in targets:
        print(f"> {u}")
        try:
            print(f"  -> {process(u)}")
        except Exception as e:
            print(f"  ! errore: {e}")
            continue          # non marcare come visto: riprova al giro dopo
        seen.add(u)
        time.sleep(3)         # gentile col sito della federazione

    if not forced:
        json.dump(sorted(seen)[-500:], open(SEEN_FILE, "w"), indent=1)
