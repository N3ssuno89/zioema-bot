"""Roba condivisa fra check_sources.py e harvest.py."""
import re
from urllib.parse import urlparse

# Un URL non e' un articolo se e' un file statico o sta in una cartella di build.
ASSET_EXT = re.compile(r"\.(?:css|js|mjs|json|xml|txt|woff2?|ttf|otf|eot|svg|png|jpe?g|"
                       r"gif|webp|avif|ico|pdf|mp4|webm|mp3|zip|webmanifest)(?:$|\?)", re.I)
ASSET_DIR = re.compile(r"/(?:_next|_nuxt|wp-content|wp-includes|wp-json|assets|static|"
                       r"sitevision|storage/build|favicon|dist|build|fonts?|css|js|img|"
                       r"images|media|uploads|feed|rss|sitemap)(?:/|$)", re.I)

def is_asset(url):
    p = urlparse(url)
    return bool(ASSET_EXT.search(p.path + ("?" + p.query if p.query else "")) or ASSET_DIR.search(p.path))


def expand_sitemap(xml, get_fn, home, depth=0):
    """Un sitemap.xml puo' essere un INDICE di altri sitemap (usavolleyball.org lo e').
    Scende di un livello e concatena i figli."""
    locs = re.findall(r"<loc>([^<]+)</loc>", xml or "")
    figli = [u for u in locs if u.lower().endswith((".xml", ".xml.gz"))][:8]
    if not figli or depth > 1:
        return xml
    fuso = xml
    for f in figli:
        r = get_fn(f)
        sub = r[1] if isinstance(r, tuple) else r
        if sub and "<loc>" in sub:
            fuso += sub
    return fuso


def estrai_json(txt, atteso="oggetto"):
    """Il modello a volte antepone una frase ('Ecco il JSON:') o incapsula in
    ```json. json.loads() su quel testo esplode con 'Expecting value: line 1
    column 1'. Qui si ritaglia la prima struttura JSON bilanciata che si trova."""
    import json
    t = re.sub(r"```(?:json)?", "", txt or "").strip()
    apri, chiudi = ("[", "]") if atteso == "lista" else ("{", "}")
    i = t.find(apri)
    if i == -1:
        raise ValueError(f"nessun JSON nella risposta. Ricevuto: {t[:300]!r}")
    livello, in_str, esc = 0, False, False
    for j in range(i, len(t)):
        ch = t[j]
        if in_str:
            if esc:      esc = False
            elif ch == "\\": esc = True
            elif ch == '"':  in_str = False
            continue
        if ch == '"':      in_str = True
        elif ch == apri:   livello += 1
        elif ch == chiudi:
            livello -= 1
            if livello == 0:
                return json.loads(t[i:j + 1])
    raise ValueError(f"JSON troncato (max_tokens?). Inizio: {t[i:i+200]!r}")
