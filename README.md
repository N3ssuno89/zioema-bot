# ZioEMA bot — monitor federvolley → Telegram

**Repo:** `zioema-bot` (privato). Gira da solo, ogni 15 minuti. Quando esce una notizia beach nuova ti arrivano su Telegram
**tre messaggi**: la grafica, la caption da sola (così la copi con un tap), e link + note.
Su Instagram carichi tu.

## Non serve un LLM per capire se è beach volley
La categoria è **stampata sulla pagina dell'articolo** ("Beach Volley" sopra il titolo).
Il bot la legge con una regex: costo zero, zero errori di classificazione.
Il modello lo paghi solo per il titolo e la caption, che è l'unica cosa che non sa fare il sito.

## Aggiungere un sito
Un blocco in `sources.json`. Nessuna riga di codice.

```json
{
  "id": "cev", "fonte": "cev.eu", "lang": "en",
  "home": "https://www.cev.eu",
  "feeds": [], "listings": ["https://www.cev.eu/beach-volleyball/"],
  "url_pattern": "^/beach-volleyball/.+", "sempre_rilevante": false, "tag": "INTL"
}
```
`feeds: []` = il bot cerca da solo RSS/Atom/sitemap. Le `listings` sono il ripiego
se il sito non ha feed. `sempre_rilevante: true` solo per il circuito italiano.

## Come filtra
1. **Parole chiave** (gratis) — scarta indoor e altri sport. Sbaglia per eccesso apposta.
2. **Triage col modello** — una sola chiamata per tutti i candidati del giro. Decide
   cosa merita un post secondo le regole editoriali.
3. **Scrittura** — una chiamata per i sopravvissuti. È l'unico stadio caro.

Su 10 titoli reali di pzps.pl: 6 muoiono allo stadio 1, ~1 sopravvive allo stadio 2.

## Setup (una volta, ~20 minuti)

**1. Repo GitHub privato** con dentro:
```
zioema_bot.py
zioema_post.py
fonts/Anton-Regular.ttf
fonts/Archivo-Bold.ttf
zioema.png                      (il logo, ancora da darmi)
seen.json                       (contenuto iniziale: [])
.github/workflows/zioema.yml    (il file in github-workflow/)
```

**2. Bot Telegram**
- scrivi a `@BotFather` → `/newbot` → ti dà il TOKEN
- scrivi un messaggio al tuo bot
- apri `https://api.telegram.org/bot<TOKEN>/getUpdates` → dentro c'è `chat.id`

**3. Secrets del repo** (Settings → Secrets and variables → Actions):
`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

**4. Prova prima a secco.** Sul tuo computer:
```bash
python3 harvest.py --dry-run
python3 zioema_bot.py https://www.federvolley.it/bpt-elite16-amburgo-gottardiorsi-toth-volano-ai-quarti-di-finale
```
Il secondo comando manda davvero su Telegram e ti fa vedere com'è fatto un messaggio.

## Cosa ho verificato e cosa no

✅ **Pagine articolo**: rendono lato server. Titolo, `og:image`, corpo, categoria e link
correlati si estraggono con `requests`. Testato sull'articolo dei quarti.

⚠️ **Pagine elenco**: il sito è Next.js e alcune liste sono renderizzate lato client —
con `requests` potresti vedere zero link. Per questo `discover()` prova quattro URL in
ordine e poi ripiega sulla sitemap. **Lancia `--dry-run` e guarda quale riga "elenco:"
stampa.** Se non ne trova nessuno, vedi sotto.

### Se discover non trova nulla
1. apri una lista nel browser, F12 → Network → cerca una chiamata `/_next/data/.../*.json`
2. quell'URL restituisce JSON con gli articoli: mettilo in cima a `LISTINGS`
3. contiene il `buildId`, che cambia a ogni deploy del sito → vai di sitemap, è più stabile

## Le due cose che rompono davvero

**Il cron di GitHub Actions non è puntuale.** Quando la piattaforma è carica salta o
ritarda anche di 10-20 minuti. Con `*/15` significa che nel caso peggiore arrivi mezz'ora
dopo la notizia. Se essere primo ti conta davvero, serve un VPS da 4€/mese con cron vero.

**Il workflow si disattiva dopo 60 giorni di repo fermo.** Il commit di `seen.json` lo
tiene sveglio finché escono notizie, ma d'inverno il beach si ferma. A gennaio controlla.

## Cose da tarare sulle prime notizie
- La costante `4.5` in `smart_crop` (stima della linea vita dal volto). Guarda le prime
  10 grafiche e alzala o abbassala.
- Il `SYSTEM` prompt in `zioema_bot.py`: se il titolo non ti piace, il problema è lì,
  non nel modello. È il file più importante di tutti.

## Sicurezza
- `seen.json` viene ricommittato dal bot: se lo cancelli, ti rimanda tutto lo storico.
- Se un articolo va in errore **non** viene marcato come visto: ci riprova al giro dopo.
- `concurrency` nel workflow evita run sovrapposte, quindi niente post doppi.
- 3 secondi tra un articolo e l'altro, User-Agent identificabile. Non martellare il sito
  di una federazione con cui vuoi lavorare.

## Costi
~0,01€ a notizia di API. GitHub Actions gratis sotto i 2.000 minuti/mese, e questo job
ne consuma ~1 a run: circa 100 al mese se gira ogni 15 minuti solo nei mesi di stagione.
