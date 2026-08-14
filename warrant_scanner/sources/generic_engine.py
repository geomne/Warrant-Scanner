"""
Generisk motor som hämtar warranter från antingen:
  - en JSON-API (type="json"), eller
  - en HTML-sida med en tabell/lista av produkter (type="html")

Varje källa beskrivs helt i config.py. Om en sajt ändrar sin struktur
behöver du bara uppdatera config.py — inte skriva ny kod.
"""
from __future__ import annotations
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from .base import fetch, fetch_json, SourceError
from models import Warrant

logger = logging.getLogger("warrant_scanner")


def _get_path(obj: Any, path: str):
    """Enkel dotted-path-uppslagning i JSON, t.ex. 'data.items' eller '0.name'."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def run_json_source(cfg: dict, underlying: str) -> list[Warrant]:
    url = cfg["url"].format(underlying=underlying)
    params = {k: v.format(underlying=underlying) for k, v in cfg.get("params", {}).items()}
    data = fetch_json(url, params=params, headers=cfg.get("headers"))
    items = _get_path(data, cfg["items_path"]) or []
    fields = cfg["fields"]
    out = []
    for item in items:
        try:
            out.append(Warrant(
                source=cfg["display_name"],
                issuer=_get_path(item, fields.get("issuer", "")) if fields.get("issuer") else cfg.get("issuer"),
                name=str(_get_path(item, fields["name"])),
                underlying=underlying,
                isin=str(_get_path(item, fields.get("isin", "")) or "") or None,
                direction=str(_get_path(item, fields.get("direction", "")) or "") or None,
                leverage=_to_float(_get_path(item, fields.get("leverage", ""))),
                stop_loss=_to_float(_get_path(item, fields.get("stop_loss", ""))),
                last_price=_to_float(_get_path(item, fields.get("last_price", ""))),
                currency=str(_get_path(item, fields.get("currency", "")) or cfg.get("currency", "SEK")),
                url=cfg.get("product_url_template", "").format(**{
                    "id": _get_path(item, fields.get("id", "")) or ""
                }) or None,
            ))
        except Exception as exc:  # en trasig rad ska inte stoppa resten
            logger.warning("[%s] hoppade över en rad: %s", cfg["display_name"], exc)
    return out


def _make_soup(html_text: str) -> BeautifulSoup:
    """Använd lxml om det finns installerat, annars Pythons inbyggda parser."""
    try:
        return BeautifulSoup(html_text, "lxml")
    except Exception:
        logger.warning("lxml saknas eller kunde inte användas — faller tillbaka på "
                        "inbyggda html.parser (lite långsammare, men fungerar).")
        return BeautifulSoup(html_text, "html.parser")


def parse_rows_from_html(html_text: str, cfg: dict, underlying: str) -> list[Warrant]:
    """Delad parsningslogik för både ren requests-HTML och browser-renderad HTML."""
    soup = _make_soup(html_text)
    rows = soup.select(cfg["row_selector"])
    if not rows:
        raise SourceError(
            f"[{cfg['display_name']}] Inga rader hittades med selektor "
            f"'{cfg['row_selector']}' — sajten kan ha ändrat sin HTML-struktur, eller "
            f"(om type='browser') väntade elementet aldrig upp. "
            f"Uppdatera config.py (se README, avsnitt 'Fixa en trasig källa')."
        )
    sel = cfg["field_selectors"]
    out = []
    for row in rows:
        try:
            def text_of(css: str | None) -> str | None:
                if not css:
                    return None
                el = row.select_one(css)
                return el.get_text(strip=True) if el else None

            name = text_of(sel.get("name"))
            if not name:
                continue
            link_el = row.select_one(sel["name"]) if sel.get("name") else None
            href = link_el.get("href") if link_el and link_el.has_attr("href") else None
            if href and href.startswith("/"):
                href = cfg.get("base_url", "").rstrip("/") + href

            out.append(Warrant(
                source=cfg["display_name"],
                issuer=text_of(sel.get("issuer")) or cfg.get("issuer"),
                name=name,
                underlying=underlying,
                isin=text_of(sel.get("isin")),
                direction=text_of(sel.get("direction")),
                leverage=_to_float(text_of(sel.get("leverage"))),
                stop_loss=_to_float(text_of(sel.get("stop_loss"))),
                last_price=_to_float(text_of(sel.get("last_price"))),
                currency=text_of(sel.get("currency")) or cfg.get("currency", "SEK"),
                url=href,
            ))
        except Exception as exc:
            logger.warning("[%s] hoppade över en rad: %s", cfg["display_name"], exc)
    return out


def run_html_source(cfg: dict, underlying: str) -> list[Warrant]:
    url = cfg["url"].format(underlying=underlying)
    params = {k: v.format(underlying=underlying) for k, v in cfg.get("params", {}).items()}
    resp = fetch(url, params=params, headers=cfg.get("headers"))
    return parse_rows_from_html(resp.text, cfg, underlying)


def run_browser_source(cfg: dict, underlying: str) -> list[Warrant]:
    """
    Använder en headless webbläsare (Playwright) för sajter som kräver att man
    faktiskt skriver i en sökruta / renderar innehåll med JavaScript, och där
    en enkel requests-anrop blockeras eller inte returnerar data.

    Kräver: pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as exc:
        raise SourceError(
            f"[{cfg['display_name']}] Playwright är inte installerat. "
            f"Kör: pip install playwright && playwright install chromium"
        ) from exc

    url = cfg["url"].format(underlying=underlying)
    timeout_ms = cfg.get("timeout_ms", 12000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=cfg.get("headless", True), args=["--disable-http2"])
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ))

            try:
                page.goto(url, wait_until=cfg.get("wait_until", "domcontentloaded"), timeout=timeout_ms)
            except PWTimeout as exc:
                browser.close()
                raise SourceError(
                    f"[{cfg['display_name']}] Timeout vid page.goto('{url}') — sidan laddade "
                    f"inte klart inom {timeout_ms}ms. Antingen är sajten långsam/blockerar "
                    f"headless-trafik, eller så är URL:en fel. Testa med headless=False i "
                    f"config.py för att se vad som händer."
                ) from exc

            # Cookie-banners m.m. som annars kan blockera interaktion, t.ex.
            # ["text=Avböj"] eller ["#onetrust-reject-all-handler"]. Playwright
            # tolkar "text=..." som en riktig selektor-motor, inget extra behövs.
            for sel in cfg.get("pre_click_selectors", []):
                try:
                    page.locator(sel).first.click(timeout=4000)
                    page.wait_for_timeout(400)
                except Exception:
                    pass  # banner fanns inte den här gången — helt ok, gå vidare

            # Om sajten behöver att man skriver i en sökruta (t.ex. Avanzas
            # typeahead-sök) istället för att ta en aktie via query-param:
            if cfg.get("search_input_selector"):
                try:
                    page.fill(cfg["search_input_selector"], underlying, timeout=timeout_ms)
                except PWTimeout as exc:
                    browser.close()
                    raise SourceError(
                        f"[{cfg['display_name']}] Timeout: hittade inte sökrutan med selektor "
                        f"'{cfg['search_input_selector']}' inom {timeout_ms}ms. Den här "
                        f"selektorn är en OVERIFIERAD PLACEHOLDER — öppna sajten i din vanliga "
                        f"webbläsare, högerklicka på sökfältet -> Inspect, och uppdatera "
                        f"search_input_selector i config.py till det du ser där "
                        f"(t.ex. 'input#search' eller 'input.search-field')."
                    ) from exc
                if cfg.get("submit_selector"):
                    page.click(cfg["submit_selector"])
                else:
                    page.keyboard.press("Enter")

            wait_selector = cfg.get("wait_selector", cfg["row_selector"])
            try:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except PWTimeout:
                pass  # låt parse_rows_from_html ge ett tydligt SourceError istället

            # liten extra paus så eventuell efterladdning hinner klart
            page.wait_for_timeout(cfg.get("settle_ms", 800))

            html = page.content()
            browser.close()
            return parse_rows_from_html(html, cfg, underlying)
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"[{cfg['display_name']}] Browser-hämtning misslyckades: {exc}") from exc


def extract_label_value_fields(html_text: str, label_map: dict[str, list[str]]) -> dict[str, str]:
    """
    Generisk 'läs etikett -> hämta närliggande värde'-extraktion. Fungerar
    för flera mönster vi sett i praktiken:
      - <dt>ISIN</dt><dd>NLBNPSE1ZNX8</dd>                     (Avanza, "Om produkten": 1 nivå)
      - <div><span>ISIN</span><div>...</div></div>              (enkel etikett/värde-panel: 1 nivå)
      - <div><span><span>Stopp Loss nivå</span></span><span>292,47</span></div>
        (Nordnet: etiketttexten sitter EN extra nivå djupare inuti en
        wrapper-span, så värdet är syskon till wrappern — inte till
        textens direkta förälder)

    Löser detta genom att, om det direkta syskonet är tomt, klättra uppåt
    genom föräldrar (några nivåer) och prova syskonet på varje nivå,
    tills ett icke-tomt värde hittas. En längdgräns på klättrade träffar
    skyddar mot att råka plocka upp en hel sektion av text av misstag.

    label_map: {"leverage": ["Indikativ hävstång", "Hävstång"], "stop_loss": [...], ...}
    Returnerar {"leverage": "8,0", ...} med bara de fält som faktiskt hittades.
    """
    soup = _make_soup(html_text)
    found: dict[str, str] = {}
    for field, label_variants in label_map.items():
        for label in label_variants:
            target = label.strip().lower()
            for text_node in soup.find_all(string=lambda s: s and s.strip().lower() == target):
                el = text_node.parent
                for level in range(4):   # 0 = direkt syskon, upp till 3 nivåer uppåt
                    if el is None:
                        break
                    sib = el.find_next_sibling()
                    if sib is not None:
                        value = sib.get_text(strip=True)
                        # på klättrade nivåer (level > 0): skydda mot att råka
                        # plocka upp en hel textsektion istället för ett värde
                        if value and (level == 0 or len(value) <= 60):
                            found[field] = value
                            break
                    el = el.parent
                if field in found:
                    break
            if field in found:
                break
    return found


def extract_label_value_fields_playwright(page, label_map: dict[str, list[str]],
                                            row_selector: str = "div.table-row") -> dict[str, str]:
    """
    Samma idé som extract_label_value_fields, men körs direkt mot den levande
    sidan via Playwright istället för mot en HTML-sträng. Behövs för sajter
    (t.ex. Nasdaq Nordic) som renderar datan inuti en Shadow DOM-komponent —
    Playwrights CSS-motor tränger igenom Shadow DOM automatiskt, men en vanlig
    HTML-parser (BeautifulSoup på page.content()) ser aldrig innehållet alls.
    """
    found: dict[str, str] = {}
    rows = page.locator(row_selector)
    try:
        count = rows.count()
    except Exception:
        return found
    for i in range(count):
        try:
            texts = [t.strip() for t in rows.nth(i).locator("*").all_text_contents()]
            texts = [t for t in texts if t]
        except Exception:
            continue
        if len(texts) < 2:
            continue
        label, value = texts[0], texts[1]
        for field, variants in label_map.items():
            if field in found:
                continue
            if any(label.lower() == v.lower() for v in variants):
                found[field] = value
    return found


import os
import time as _time


def _debug_dump(page, cfg: dict, note: str) -> str:
    """Sparar en skärmdump när ett sök-steg misslyckas, så vi kan se exakt
    vad webbläsaren såg utan att behöva be användaren spela in på nytt."""
    try:
        os.makedirs("debug", exist_ok=True)
        fname = f"debug/{cfg['display_name'].lower().replace(' ', '_')}_{note}_{int(_time.time())}.png"
        page.screenshot(path=fname, full_page=True)
        return fname
    except Exception:
        return ""


def _do_search_and_collect_links(page, cfg: dict, underlying: str, timeout_ms: int,
                                   category_selector: str | None = None,
                                   query_template: str | None = None) -> list[str]:
    """Kör sök-flödet en gång (öppna sida, sök, ev. klicka en kategori) och
    returnerar de produktlänkar som blev synliga."""
    from playwright.sync_api import TimeoutError as PWTimeout

    for attempt in range(2):
        try:
            page.goto(cfg["search_url"], wait_until=cfg.get("wait_until", "domcontentloaded"),
                       timeout=timeout_ms)
            break
        except Exception as exc:
            if attempt == 0:
                logger.warning("[%s] page.goto misslyckades (%s), försöker igen ...",
                                cfg["display_name"], exc)
                page.wait_for_timeout(1500)
                continue
            shot = _debug_dump(page, cfg, "goto_failed")
            extra = f" Skärmdump sparad: {shot}" if shot else ""
            raise SourceError(
                f"[{cfg['display_name']}] Kunde inte ladda '{cfg['search_url']}': {exc}.{extra}"
            ) from exc

    for sel in cfg.get("pre_click_selectors", []):
        try:
            page.locator(sel).first.click(timeout=4000)
            page.wait_for_timeout(400)
        except Exception:
            pass

    if not cfg.get("search_input_selector"):
        raise SourceError(
            f"[{cfg['display_name']}] search_input_selector är inte ifylld i config.py."
        )
    search_text = (query_template or cfg.get("search_query_template", "{underlying}")).format(underlying=underlying)
    try:
        page.fill(cfg["search_input_selector"], search_text, timeout=timeout_ms)
    except PWTimeout as exc:
        shot = _debug_dump(page, cfg, "search_timeout")
        extra = f" Skärmdump sparad: {shot}" if shot else ""
        raise SourceError(
            f"[{cfg['display_name']}] Timeout: hittade inte sökrutan med selektor "
            f"'{cfg['search_input_selector']}' inom {timeout_ms}ms.{extra}"
        ) from exc

    for action in cfg.get("post_fill_actions", []):
        loc = page.locator(action["selector"])
        loc = loc.nth(action["nth"]) if "nth" in action else loc.first
        for _ in range(action.get("times", 1)):
            try:
                loc.click(timeout=action.get("timeout", 3000))
                page.wait_for_timeout(action.get("wait_after", 300))
            except Exception:
                break   # knappen finns inte längre — troligen redan alla resultat laddade

    if category_selector:
        try:
            page.locator(category_selector).first.click(timeout=4000)
            page.wait_for_timeout(500)
        except Exception:
            shot = _debug_dump(page, cfg, "category_not_found")
            extra = f" Skärmdump sparad: {shot}" if shot else ""
            logger.warning("[%s] Kategoriknapp '%s' hittades inte — hoppar över den kategorin.%s",
                            cfg["display_name"], category_selector, extra)
            return []

    page.wait_for_timeout(cfg.get("settle_ms", 1000))

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    must_contain = cfg["result_href_must_contain"]
    exclude_suffixes = cfg.get("result_href_exclude_suffixes", [])
    base_url = cfg.get("base_url", "")
    urls = []
    for href in hrefs:
        if not href or must_contain not in href:
            continue
        if any(href.rstrip("/").endswith(suf) for suf in exclude_suffixes):
            continue
        full = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        urls.append(full)
    return urls


def run_browser_products_source(cfg: dict, underlying: str) -> list[Warrant]:
    """
    Flöde för sajter utan en ren tabell-lista (Avanza, Nordnet, Nasdaq Nordic):
      1) Sök på sajten, ev. en gång per kategori (cfg["category_filters"]),
         samla länkar till enskilda produktsidor.
      2) Besök varje produktsida och läs av etikett/värde-fälten där —
         antingen via vanlig HTML (extract_label_value_fields) eller, för
         Shadow DOM-sidor, direkt via Playwright
         (extract_label_value_fields_playwright, cfg["extract_via_playwright"]=True).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceError(
            f"[{cfg['display_name']}] Playwright är inte installerat. "
            f"Kör: pip install playwright && playwright install chromium"
        ) from exc

    timeout_ms = cfg.get("timeout_ms", 12000)
    warrants: list[Warrant] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=cfg.get("headless", True), args=["--disable-http2"])
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ))

            # --- Steg 1: sök, ev. en gång per (sökfras x kategori) ---
            query_templates = cfg.get("search_query_templates") or [cfg.get("search_query_template", "{underlying}")]
            category_filters = cfg.get("category_filters") or [None]
            seen = set()
            product_urls: list[str] = []
            for query_template in query_templates:
                for cat_sel in category_filters:
                    try:
                        urls = _do_search_and_collect_links(page, cfg, underlying, timeout_ms,
                                                              cat_sel, query_template)
                    except SourceError:
                        browser.close()
                        raise
                    for u in urls:
                        if u not in seen:
                            seen.add(u)
                            product_urls.append(u)

            product_urls = product_urls[: cfg.get("max_products", 15)]
            if not product_urls:
                shot = _debug_dump(page, cfg, "no_links_found")
                extra = f" Skärmdump sparad: {shot}" if shot else ""
                browser.close()
                raise SourceError(
                    f"[{cfg['display_name']}] Sökningen på '{underlying}' gav inga produktlänkar "
                    f"som innehöll '{cfg['result_href_must_contain']}'.{extra}"
                )

            logger.info("[%s] Hittade %d produktlänkar för '%s'", cfg["display_name"],
                        len(product_urls), underlying)

            # --- Steg 2: besök varje produktsida ---
            product_debug_dumped = False
            for product_url in product_urls:
                try:
                    page.goto(product_url, wait_until=cfg.get("wait_until", "domcontentloaded"),
                               timeout=timeout_ms)
                    if cfg.get("product_wait_selector"):
                        try:
                            page.wait_for_selector(cfg["product_wait_selector"],
                                                     timeout=cfg.get("product_wait_timeout_ms", 8000))
                        except Exception:
                            logger.warning("[%s] product_wait_selector '%s' syntes aldrig på %s "
                                            "inom tidsgränsen.", cfg["display_name"],
                                            cfg["product_wait_selector"], product_url)
                    page.wait_for_timeout(cfg.get("product_settle_ms", 500))

                    if cfg.get("extract_via_playwright"):
                        fields = extract_label_value_fields_playwright(
                            page, cfg["label_map"], cfg.get("shadow_row_selector", "div.table-row"))
                    else:
                        fields = extract_label_value_fields(page.content(), cfg["label_map"])

                    missing = [f for f in ("leverage", "stop_loss") if f not in fields]
                    if missing:
                        extra = ""
                        if not product_debug_dumped:
                            shot = _debug_dump(page, cfg, "product_fields_missing")
                            if shot:
                                extra = f" Skärmdump sparad: {shot}"
                                product_debug_dumped = True   # bara en per körning, annars svämmar debug/ över
                        logger.warning("[%s] %s: fälten %s hittades inte på %s.%s",
                                        cfg["display_name"], underlying, missing, product_url, extra)

                    name = fields.get("name") or _title_from_url(product_url)
                    warrants.append(Warrant(
                        source=cfg["display_name"],
                        issuer=fields.get("issuer") or cfg.get("issuer"),
                        name=name,
                        underlying=underlying,
                        isin=fields.get("isin"),
                        direction=fields.get("direction"),
                        leverage=_to_float(fields.get("leverage")),
                        stop_loss=_to_float(fields.get("stop_loss")),
                        last_price=_to_float(fields.get("last_price")),
                        currency=fields.get("currency") or cfg.get("currency", "SEK"),
                        url=product_url,
                    ))
                except Exception as exc:
                    logger.warning("[%s] hoppade över %s: %s", cfg["display_name"], product_url, exc)

            browser.close()
            return warrants
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"[{cfg['display_name']}] Browser-hämtning misslyckades: {exc}") from exc


def _title_from_url(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return tail.replace("-", " ").title()


def run_source(cfg: dict, underlying: str) -> list[Warrant]:
    if cfg["type"] == "json":
        return run_json_source(cfg, underlying)
    elif cfg["type"] == "html":
        return run_html_source(cfg, underlying)
    elif cfg["type"] == "browser":
        return run_browser_source(cfg, underlying)
    elif cfg["type"] == "browser_products":
        return run_browser_products_source(cfg, underlying)
    else:
        raise SourceError(f"Okänd källtyp: {cfg['type']}")
