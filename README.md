# Warrant Scanner

Sök igenom flera källor efter warranter/mini-futures för en vald underliggande
aktie och få hävstång, stop loss/knock-out-nivå och pris i en samlad lista.

## Snabbstart

```bash
pip install -r requirements.txt
playwright install chromium      # krävs för Avanza/Nordnet (browser-baserade källor)
python main.py "Volvo B"
```

**Flera aktier samtidigt** (en tabell + en CSV-fil per aktie):

```bash
python main.py "Volvo B" "Ericsson B" "Investor B" --csv-dir resultat/
```

Kör kontinuerligt var 30:e minut med en artig paus på 3 sekunder mellan varje anrop:

```bash
python main.py "Volvo B" "Ericsson B" --interval 1800 --delay 3 --csv-dir resultat/
```

Alla flaggor:

```bash
python main.py --help
```

## Bevakningsläge (flera aktier, filtrerat och sorterat)

Utöver att söka enstaka aktier kan scriptet bevaka en hel lista med
aktier på en gång:

```bash
python main.py --watchlist watchlist.txt
python main.py --watchlist watchlist.txt --csv bevakning.csv
python main.py --watchlist watchlist.txt --interval 1800   # uppdatera var 30:e minut
```

`watchlist.txt` (redigera den medföljande filen, eller skapa en egen):
en aktie per rad. Om den automatiska tickerigenkänningen (via Yahoo
Finance-sök) skulle gissa fel aktie, ange tickern manuellt:

```
Tesla
Nvidia
Volvo B :: VOLV-B.ST
```

För varje aktie: hämtar scriptet aktuellt pris (Yahoo Finance), söker
igenom alla warranter precis som i vanligt läge, och filtrerar sedan:

- **Warranter utan känd stop loss eller riktning** hoppas över helt
  (kan inte avgöras om de är levande, och kan inte rankas).
- **"Döda" warranter filtreras bort:** för en Lång/Bull-warrant räknas
  den som död om aktiekursen gått ner till eller under stop loss-nivån;
  för en Kort/Bear-warrant är det tvärtom — död om kursen gått upp till
  eller över stop loss-nivån.
- **Warranter vars stop loss ligger för långt bort filtreras också
  bort** — standard 35% av aktiekursen (justera med `--max-distance-pct`,
  t.ex. `--max-distance-pct 50` eller sätt högt som `1000` för att stänga
  av filtret helt).
- De som blir kvar från **alla** aktier i listan slås ihop till EN
  gemensam tabell, sorterad efter hur nära (i procent av aktiekursen)
  varje warrant är sin stop loss just nu — de mest riskfyllda/aktuella
  ligger högst upp.

**Viktig brasklapp om valuta:** aktiekursen hämtas i den valuta Yahoo
Finance anger för aktien (USD för t.ex. Tesla/Nvidia, SEK för
Stockholmsbörsen-noterade aktier som Volvo B). Jämförelsen mot
warrantens stop loss-nivå förutsätter att Avanza/Nordnet visar den
nivån i samma valuta som aktiens naturliga notering — det stämde med
allt vi sett hittills, men jag har inte kunnat verifiera det för alla
tänkbara aktier/valutor. Om något ser fel ut (orimligt stort/litet
procentavstånd), kontrollera valutan i loggen mot vad som faktiskt
visas på warrantens sida.

**Också värt att veta:** jag har inte kunnat testa `pricing.py` mot
Yahoo Finance live (samma nätverksbegränsning som resten av projektet
— query1/query2.finance.yahoo.com är inte nåbara härifrån). Logiken är
testad mot Yahoo:s kända, väldokumenterade svarsformat, men detta är
första gången den körs mot riktig data. Om ett aktienamn inte hittas
eller ett pris inte går att hämta loggas det tydligt och den aktien
hoppas bara över — resten av listan körs som vanligt.

## Om källorna: JSON-API vs headless browser

Efter att Avanzas och Nordnets publika/interna API:er visat sig vara
svåra att nå direkt (troligen bot-skydd eller att de stängts ner för
icke-inloggad/extern trafik) använder scriptet nu **headless browser
(Playwright)** som förstahandsspår för just de två källorna:
scriptet öppnar sidan i en riktig (osynlig) Chromium-webbläsare, skriver
in aktienamnet i sökrutan precis som en människa skulle göra, och läser
sedan av det renderade resultatet. Det är långsammare än rena API-anrop
men mycket svårare för sajterna att skilja från en vanlig besökare.

Nasdaq Nordic (och eventuella emittentsajter du lägger till) försöker
fortfarande först med vanliga, snabba `requests`-anrop (`type="html"`
eller `type="json"` i `config.py`) — byt till `type="browser"` för en
sådan källa också om den blockerar enkla anrop.

## Viktigt att veta innan du kör

**Jag har inte kunnat testa de riktiga källorna live.** Miljön jag skrev
koden i har bara nätverksåtkomst till paketkällor (pypi/npm/github), inte
till avanza.se, nordnet.se eller nasdaqomxnordic.com. När jag körde
scriptet i min sandbox fick jag `403 Forbidden` på alla tre — men det var
min egen nätverksspärr som stoppade anropen (se `x-deny-reason` i det
scenariot), inte nödvändigtvis sajternas svar. **Du behöver alltså köra
och felsöka scriptet på din egen dator där det har vanlig internetåtkomst.**

Utöver det finns två saker som *garanterat* kommer kräva justering:

1. **Ingen av dessa sajter har ett officiellt, dokumenterat publikt API.**
   `config.py` innehåller väl underbyggda startgissningar på URL:er och
   fältnamn, men de kan vara fel eller inaktuella.
2. **Bot-skydd.** Avanza, Nordnet och Nasdaq Nordic använder ofta
   Cloudflare/WAF-skydd som kan blockera enkla `requests`-anrop även med
   rätt URL. Om du får riktiga 403/429-fel (inte från min sandbox, utan på
   din egen dator) är nästa steg oftast att byta till en headless
   webbläsare (t.ex. `playwright`) som kör med en riktig webbläsarmotor,
   se avsnittet nedan.

## Hur Avanza och Nordnet faktiskt fungerar

De har ingen ren tabell-lista med alla warranter för en aktie. Istället
kör scriptet ett tvåstegsflöde (`type="browser_products"` i `config.py`):

1. **Sök** på sajten efter aktien, samla ihop länkarna till de enskilda
   produktsidorna som dyker upp i sökresultaten (filtreras via
   `result_href_must_contain`, t.ex. `warranter-torg/om-warranten.html`
   för Avanza eller `/etp/warranter/` för Nordnet).
2. **Besök varje produktsida** och läs av "Om produkten"/
   "Produktinformation"-rutan genom att matcha etikettext (t.ex. "ISIN",
   "Stop Loss", "Hävstång") och hämta värdet som står bredvid/under —
   samma logik oavsett om sajten använder `<dt>/<dd>` eller
   `<span>/<div>`-par. Den här funktionen
   (`extract_label_value_fields` i `sources/generic_engine.py`) är
   testad mot riktig HTML från båda sajterna.

Det som kan behöva fyllas i är **sökrutans selektor** per sajt
(`search_input_selector` i `config.py`) — se nästa avsnitt.

## Fixa en trasig källa

**Nytt: automatiska debug-skärmdumpar.** Om ett sök-steg misslyckas
(sökrutan hittas inte, en kategoriknapp hittas inte, eller sökningen ger
noll produktlänkar) sparar scriptet nu automatiskt en skärmdump i mappen
`debug/` bredvid scriptet — felmeddelandet i loggen talar om exakt
vilken fil. Skicka den bilden direkt istället för att behöva köra
`playwright codegen` på nytt.


1. Öppna sajten i Chrome/Firefox, öppna DevTools → fliken **Network**.
2. Sök på önskad aktie i sajtens eget sökfält för warranter/certifikat.
3. Titta i Network-fliken efter anropet som hämtar listan:
   - Om det är ett `Fetch/XHR`-anrop som returnerar JSON → kopiera URL:en
     och undersök JSON-strukturen. Uppdatera källans `url`, `params`,
     `items_path` och `fields` i `config.py` så de matchar.
   - Om sidan renderas som vanlig HTML (visa källkod, Ctrl+U) → uppdatera
     `row_selector` och `field_selectors` så CSS-selektorerna pekar på
     rätt kolumner i tabellen.
4. Kör `python main.py "<aktie>"` igen och kolla loggen.

### Om en källa har `type="browser"` (Avanza/Nordnet) och inte hittar rader

1. Kör scriptet en gång och notera vilken `wait_selector`/`row_selector`
   som gav noll träffar.
2. Öppna sajten manuellt, sök på en aktie du vet har warranter, och
   inspektera (Ctrl+Shift+I → Elements) exakt vilken CSS-selektor
   sökrutan har (`search_input_selector`) och vilken selektor
   resultatraderna har (`row_selector`, samt `field_selectors` för varje
   kolumn).
3. Uppdatera motsvarande källa i `config.py`.
4. Vill du se vad webbläsaren faktiskt gör medan scriptet kör, sätt
   `headless=False` temporärt i `sources/generic_engine.py` →
   `run_browser_source` — då öppnas ett synligt webbläsarfönster du kan
   följa steg för steg.

### Om en helt vanlig `requests`-källa (t.ex. Nasdaq Nordic) blockeras

Ändra källans `"type": "html"` till `"type": "browser"` i `config.py`
och lägg till `wait_selector` (samma som `row_selector` räcker oftast) —
motorn hanterar båda typerna med samma `field_selectors`-format.

## Lägga till en emittent (Nordea Markets, SEB, Vontobel, Société Générale, ...)

Varje emittent har sin egen sökfunktion utan gemensam mall. Lägg till en
ny post i listan `SOURCES` i `config.py` — en mall finns redan där,
inkommenterad, längst ner i filen. Följ samma steg som ovan (DevTools →
hitta sökanropet → mappa fält).

## Att tänka på juridiskt/praktiskt

- Kontrollera respektive sajts användarvillkor (ToS) och `robots.txt`
  innan du skrapar i större skala. Vissa sajter tillåter uttryckligen
  inte automatiserad datainsamling.
- Håll anropsfrekvensen låg (scriptets `--interval`-flagga finns för att
  du inte ska behöva polla oftare än nödvändigt). Standardintervallet
  30 min i din fråga är en rimlig, skonsam nivå.
- Detta är inget investeringsråd eller en garanterat korrekt datakälla —
  verifiera alltid stop loss/hävstång mot produktbladet innan du handlar.
  Warranter är högriskprodukter som kan bli värdelösa om
  stop loss-nivån/barriären nås.

## Projektstruktur

```
warrant_scanner/
├── main.py                  CLI: sökläge (1+ aktier) + bevakningsläge (--watchlist)
├── config.py                Källor: URL:er, params, selektorer/fältmappning
├── models.py                Warrant-datamodell
├── pricing.py                Aktiekurser via Yahoo Finance (för bevakningsläget)
├── watchlist.txt             Exempel-bevakningslista (redigera fritt)
├── requirements.txt
└── sources/
    ├── base.py              HTTP-hämtning med retries (requests)
    └── generic_engine.py    JSON-, HTML- och headless-browser-scraper (Playwright)
```
