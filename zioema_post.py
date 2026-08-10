#!/usr/bin/env python3
"""
ZioEMA - generatore grafica notizia (formato 1080x1350).

USO
    python3 zioema_post.py output.jpg "RIGA UNO" "RIGA DUE" ["RIGA TRE"]

Il font si dimensiona sulla riga PIU' LUNGA e applica quella misura a tutte:
tieni ogni riga tra 14 e 17 caratteri, altrimenti le corte restano mezze vuote.

PARAMETRI VIA VARIABILI D'AMBIENTE (tutti opzionali)
    SRC          percorso foto sorgente
    PHOTO_H      altezza area foto in px  (900 = fascia blu 450 -> default)
                 piu' alto = fascia blu piu' bassa MA crop piu' basso sui corpi
    CROP_TOP     prima riga di pixel da includere (taglia cielo/spazio morto)
    CROP_BOTTOM  ultima riga di pixel della foto sorgente da includere
                 tienila SOPRA la linea del costume
    CX           centro orizzontale del crop sulla foto sorgente
    FADE_START   dove inizia la sfumatura verso il blu (px, coord. finali)
    FONTE        testata da citare in basso a destra (default: federvolley)
    TOP_PAD / BOT_PAD / LINE_Y  spaziature del blocco testo

ATTENZIONE: PHOTO_H, CROP_BOTTOM e CX vanno RITARATI su ogni foto.
Non sono impostazioni fisse: dipendono da dove stanno i soggetti nello scatto.

DIPENDENZE
    pip install pillow
    font Anton: https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf
    font Archivo: https://raw.githubusercontent.com/google/fonts/main/ofl/archivo/Archivo[wdth,wght].ttf
    metti i due .ttf in ./fonts/
"""
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import sys, os

SRC         = os.environ.get("SRC", "foto.jpg")
OUT         = sys.argv[1] if len(sys.argv) > 1 else "out.jpg"
LINES       = sys.argv[2:] if len(sys.argv) > 2 else ["RIGA UNO", "RIGA DUE"]
FONTE       = os.environ.get("FONTE", "federvolley")

W, H        = 1080, 1350
PHOTO_H     = int(os.environ.get("PHOTO_H", 900))
FADE_START  = int(os.environ.get("FADE_START", 520))
CROP_TOP    = int(os.environ.get("CROP_TOP", 0))        # taglia cielo/spazio morto in alto
CROP_BOTTOM = int(os.environ.get("CROP_BOTTOM", 0))     # 0 = tutta l'altezza
CX          = int(os.environ.get("CX", 0))              # 0 = centro foto
LINE_Y      = int(os.environ.get("LINE_Y", 1245))
TOP_PAD     = int(os.environ.get("TOP_PAD", 40))
BOT_PAD     = int(os.environ.get("BOT_PAD", 105))

NAVY, YELLOW, MARGIN = (13, 27, 48), (242, 194, 48), 62
FONT_HEAD = os.environ.get("FONT_HEAD", "fonts/Anton-Regular.ttf")
FONT_UI   = os.environ.get("FONT_UI",   "fonts/Archivo-Bold.ttf")

# ---- foto ----
src = Image.open(SRC).convert("RGB")
sw, sh = src.size
bottom = CROP_BOTTOM or sh
top = CROP_TOP
cx = CX or sw // 2
crop_w = int((bottom - top) * W / PHOTO_H)
left = max(0, min(sw - crop_w, cx - crop_w // 2))
photo = src.crop((left, top, left + crop_w, bottom)).resize((W, PHOTO_H), Image.LANCZOS)
photo = ImageEnhance.Color(ImageEnhance.Contrast(photo).enhance(1.06)).enhance(1.05)
# le foto federvolley sono spesso piccole: se ingrandiamo molto, recupera un po' di nitidezza
zoom = W / crop_w
if zoom > 1.3:
    photo = photo.filter(ImageFilter.UnsharpMask(radius=1.6, percent=int(min(110, 40 * zoom)), threshold=3))

canvas = Image.new("RGB", (W, H), NAVY)
canvas.paste(photo, (0, 0))

# ---- sfumatura foto -> fascia blu ----
grad = Image.new("L", (1, H), 0); gd = grad.load()
for y in range(H):
    if y < FADE_START:            a = 0
    elif y < PHOTO_H:             a = int(255 * (((y - FADE_START) / (PHOTO_H - FADE_START)) ** 1.7))
    else:                         a = 255
    gd[0, y] = a
canvas = Image.composite(Image.new("RGB", (W, H), NAVY), canvas, grad.resize((W, H)))
draw = ImageDraw.Draw(canvas)

# ---- titolo: stessa dimensione su tutte le righe ----
COL = W - 2 * MARGIN
def fit(text, max_w, max_size=126):
    for size in range(max_size, 19, -1):
        f = ImageFont.truetype(FONT_HEAD, size)
        b = f.getbbox(text)
        if (b[2] - b[0]) <= max_w:
            return f, b
    f = ImageFont.truetype(FONT_HEAD, 20)
    return f, f.getbbox(text)

size = min(fit(ln, COL)[0].size for ln in LINES)
fonts = [ImageFont.truetype(FONT_HEAD, size) for _ in LINES]
boxes = [f.getbbox(ln) for f, ln in zip(fonts, LINES)]
heights = [b[3] - b[1] for b in boxes]
gap = int(size * 0.11)
block_h = sum(heights) + gap * (len(heights) - 1)

zone_top, zone_bot = PHOTO_H + TOP_PAD, LINE_Y - BOT_PAD
y = zone_top + max(0, zone_bot - zone_top - block_h) * 0.50

for ln, f, b, h in zip(LINES, fonts, boxes, heights):
    draw.text((MARGIN - b[0] + 4, y - b[1] + 5), ln, font=f, fill=(0, 0, 0))
    draw.text((MARGIN - b[0],     y - b[1]),     ln, font=f, fill=YELLOW)
    y += h + gap

# ---- footer: logo + riga + fonte ----
r = 34
ccx, ccy = MARGIN + r, LINE_Y - 2
logo = os.environ.get("LOGO", "zioema.png")
if logo and os.path.exists(logo):
    # Il blu dello scudo (#22218E) sul navy della fascia dà 1.38:1 di contrasto:
    # invisibile. Serve un disco chiaro dietro, che porta il logo a 12.5:1.
    lg = Image.open(logo).convert("RGBA")
    bb = lg.split()[3].getbbox()          # via il margine trasparente
    if bb:
        lg = lg.crop(bb)
    d = int(r * 2 * 0.74)                 # logo dentro al disco, con aria attorno
    lg.thumbnail((d, d), Image.LANCZOS)
    disc = Image.new("RGBA", (r * 2, r * 2), (255, 255, 255, 255))
    hole = Image.new("L", (r * 2, r * 2), 0)
    ImageDraw.Draw(hole).ellipse([0, 0, r * 2 - 1, r * 2 - 1], fill=255)
    disc.putalpha(hole)
    disc.paste(lg, ((r * 2 - lg.width) // 2, (r * 2 - lg.height) // 2), lg)
    canvas.paste(disc, (ccx - r, ccy - r), disc)
else:
    draw.ellipse([ccx - r, ccy - r, ccx + r, ccy + r], fill=(58, 62, 70))
    draw.ellipse([ccx - 11, ccy - 16, ccx + 11, ccy + 6], fill=(212, 214, 218))
    draw.pieslice([ccx - 19, ccy - 2, ccx + 19, ccy + 34], 180, 360, fill=(212, 214, 218))

draw.line([(ccx + r + 26, LINE_Y), (W - MARGIN, LINE_Y)], fill=YELLOW, width=3)
fu = ImageFont.truetype(FONT_UI, 30)
wn = draw.textlength(FONTE, font=fu)
wl = draw.textlength("FONTE: ", font=fu)
draw.text((W - MARGIN - wn, LINE_Y + 26), FONTE, font=fu, fill=YELLOW)
draw.text((W - MARGIN - wn - wl, LINE_Y + 26), "FONTE: ", font=fu, fill=(255, 255, 255))

canvas.save(OUT, quality=95, subsampling=0)
print("salvato:", OUT, "| font:", size, "px | righe:", len(LINES))
