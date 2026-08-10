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
