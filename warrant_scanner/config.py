"""
Konfiguration för alla källor.

Nasdaq Nordic är borttagen för tillfället — sajten blockerade headless
Chromium konsekvent (timeout vid varje page.goto, även efter flera
motåtgärder). Avanza och Nordnet är de källor som faktiskt går att
handla igenom, så fokus ligger där.

Varje källa är antingen:
  type="json"            -> ett internt/oskrivet JSON-API
  type="html"             -> en vanlig webbsida, hämtad med requests, scrapas med CSS-selektorer
  type="browser"          -> en enda sida renderad i headless Chromium, scrapas med CSS-selektorer
  type="browser_products" -> sök -> samla produktlänkar -> besök varje sida -> läs av etikett/värde-fält

{underlying} ersätts automatiskt med aktienamnet du anger vid körning.
"""

SOURCES = [
    # ------------------------------------------------------------------
    # AVANZA – type="browser_products": sök på avanza.se/start, samla
    # länkar till enskilda produktsidor (mönster .../warranter-torg/
    # om-warranten.html/...), besök varje sida och läs av etikett/värde-
    # fälten i "Om produkten"-rutan + toppens nyckeltalsruta.
    #
    # Fokus just nu: bara Mini Futures (bekräftat länkmönster från
    # användaren: warranter-torg/om-warranten.html/.../mini-l-...).
    # En vanlig sökning på bara "Tesla" ger ~900 träffar i Warranter-
    # kategorin, nästan alla vanliga SG-warranter utan stop loss/hävstång
    # (de saknar knock-out-barriär per definition), så Mini Futures
    # drunknar långt ner i listan. Löser det genom att söka på
    # "MINI {underlying}" istället för bara aktienamnet — Mini Futures
    # heter bokstavligen "MINI L ..."/"MINI S ..." så sökningen blir
    # mycket mer träffsäker och resultatlistan mycket kortare.
    #
    # Certifikat (BULL/BEAR, t.ex. certifikat-torg/om-certifikatet.html)
    # är en helt separat kategori/URL-mönster — läggs till senare, se
    # utkommenterad "category_filters"-rad nedan för när det är dags.
    # ------------------------------------------------------------------
    {
        "display_name": "Avanza",
        "type": "browser_products",
        # "headless": False,   # AVKOMMENTERA för att se webbläsaren jobba live vid felsökning
        "search_url": "https://www.avanza.se/start",
        "base_url": "https://www.avanza.se",
        "pre_click_selectors": ["role=button[name='Sök']"],   # öppnar sökfältet (bekräftat av din inspelning)
        "search_input_selector": "role=searchbox[name='Vad letar du efter?']",
        "search_query_templates": [
            "MINI {underlying}",           # generellt — fångar SG, BNP m.fl.
            "MINI {underlying} AVA",       # riktar specifikt mot Morgan Stanleys "AVA"-serie, som annars drunknar långt ner i en generell sökning
        ],
        "post_fill_actions": [
            {"selector": "role=button[name='Visa fler']", "times": 4},   # klicka flera gånger — annars visas mest ett issuer/SG-dominerat urval
        ],
        "category_filters": [
            "role=button[name=/^Warranter/]",
            # "role=button[name=/^Certifikat/]",   # AKTIVERA senare för BULL/BEAR-certifikat (annat URL-mönster, se ovan)
        ],
        "result_href_must_contain": "warranter-torg/om-warranten.html",
        "max_products": 40,
        "label_map": {
            "name": ["Namn"],
            "isin": ["ISIN"],
            "direction": ["Riktning", "Long/Short"],
            "leverage": ["Indikativ hävstång", "Hävstång"],
            "stop_loss": ["Stop Loss", "Stop loss"],
            "issuer": ["Utfärdare"],
            "last_price": ["Senast betalt", "Senast"],
        },
        "currency": "SEK",
    },

    # Reservspår om du ändå hittar ett fungerande internt JSON-anrop
    # (avlyssna DevTools → Network → XHR/Fetch när du söker på avanza.se):
    # {
    #     "display_name": "Avanza (JSON)",
    #     "type": "json",
    #     "url": "https://www.avanza.se/_api/....",
    #     "params": {"query": "{underlying}", "limit": "50"},
    #     "items_path": "hits",
    #     "fields": {
    #         "name": "name", "isin": "isin", "direction": "warrantType",
    #         "leverage": "leverage", "stop_loss": "stopLossLevel",
    #         "last_price": "lastPrice", "id": "id",
    #     },
    #     "product_url_template": "https://www.avanza.se/warranter/om-warranten.html/{id}",
    #     "currency": "SEK",
    # },

    # ------------------------------------------------------------------
    # NORDNET – type="browser_products": sök via topp-sökrutan (bekräftat
    # fungerande flöde från din codegen-inspelning), samla länkar till
    # enskilda produktsidor (mönster /etp/warranter/...), besök varje
    # sida och läs av "Produktinformation"-rutan. Bekräftat fungerande
    # mot en riktig produktsida (dina skärmdumpar).
    #
    # OBS: Nordnets Mini Future-sidor visar "Stopp Loss nivå" och
    # "Finansieringsnivå" men inte alltid en direkt "Hävstång"-etikett på
    # själva produktsidan (den visas i sidopanelen "Relaterade
    # värdepapper" istället). Om leverage kommer tillbaka tom för Nordnet
    # är det troligen därför — stop loss-fältet är det som är
    # huvudsaken och det fångas.
    # ------------------------------------------------------------------
    {
        "display_name": "Nordnet",
        "type": "browser_products",
        # "headless": False,   # AVKOMMENTERA för att se webbläsaren jobba live vid felsökning
        "search_url": "https://www.nordnet.se/",  # marknaden/certifikat-warranter är en död 404-sida (bekräftat av din skärmdump) — söker istället från startsidan
        "base_url": "https://www.nordnet.se",
        "pre_click_selectors": [
            "text=Avböj",                          # stänger cookie-bannern
            "role=button[name='Sök']",             # öppnar sök-modalen
        ],
        "search_input_selector": "role=textbox[name=/^Sök värdepapper/]",
        "post_fill_actions": [
            # Expanderar warrant-kategorin i sökresultaten (bekräftat index
            # från din inspelning: nth(1) = andra "Visa fler"-knappen).
            {"selector": "role=button[name='Visa fler']", "nth": 1},
        ],
        "result_href_must_contain": "/etp/warranter/",
        "result_href_exclude_suffixes": ["/lista"],   # utesluter kategorins listsidor (t.ex. .../mini-futures/lista), bara enskilda produkter ska med
        "max_products": 12,
        "product_wait_selector": "text=Nyckeldata",   # väntar tills nyckeltalsrutan (Stopp Loss nivå m.fl.) faktiskt renderats — bekräftat via din skärmdump
        "product_wait_timeout_ms": 15000,   # höjt rejält — SPA:n verkar ta längre tid än de tidigare 8000ms
        "product_settle_ms": 2000,
        "label_map": {
            "name": ["Namn"],
            "isin": ["ISIN"],
            "direction": ["Long/Short", "Riktning"],
            "leverage": ["Hävstång", "Indikativ hävstång"],
            "stop_loss": ["Stopp Loss nivå", "Stop Loss nivå", "Stop loss"],
            "issuer": ["Utfärdare"],
            "financing_level": ["Finansieringsnivå"],
        },
        "currency": "SEK",
    },

    # ------------------------------------------------------------------
    # EMITTENTERNAS EGNA SAJTER – varje bank/emittent (Nordea Markets,
    # SEB, Vontobel, Société Générale, Morgan Stanley m.fl.) har helt
    # egna sökverktyg och HTML-strukturer. Det finns ingen gemensam
    # mall, så lägg till en post per emittent här när du hittat deras
    # söksida och inspekterat HTML:en. Mall nedan (inaktiv tills ifylld):
    # ------------------------------------------------------------------
    # {
    #     "display_name": "Nordea Markets",
    #     "issuer": "Nordea",
    #     "type": "html",
    #     "url": "https://.../sok",
    #     "base_url": "https://...",
    #     "params": {"underlying": "{underlying}"},
    #     "row_selector": "table tbody tr",
    #     "field_selectors": {
    #         "name": "td:nth-child(1) a",
    #         "isin": "td:nth-child(2)",
    #         "direction": "td:nth-child(3)",
    #         "leverage": "td:nth-child(4)",
    #         "stop_loss": "td:nth-child(5)",
    #         "last_price": "td:nth-child(6)",
    #     },
    #     "currency": "SEK",
    # },
]
