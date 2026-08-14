"""
Hämtar aktuellt aktiepris via Yahoo Finance publika (odokumenterade men
väletablerade) endpoints — samma man använder som biblioteket `yfinance`
använder under huven. Ingen API-nyckel behövs.

VIKTIGT: Jag har inte kunnat testa dessa endpoints live (mitt sandbox-
nätverk når inte query1/query2.finance.yahoo.com). Endpoint-formen är
väldokumenterad i communityn och stabil sedan länge, men om Yahoo ändrar
något får du samma tydliga felmeddelande i loggen som med de andra
källorna.
"""
from __future__ import annotations
import logging
import requests

logger = logging.getLogger("warrant_scanner")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def resolve_ticker(name: str) -> str | None:
    """Slår upp en akties tickersymbol från ett namn, t.ex. 'Tesla' -> 'TSLA'."""
    try:
        resp = requests.get(SEARCH_URL, params={"q": name, "quotesCount": 5, "newsCount": 0},
                             headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for quote in data.get("quotes", []):
            if quote.get("quoteType") == "EQUITY" and quote.get("symbol"):
                return quote["symbol"]
    except Exception as exc:
        logger.warning("Kunde inte slå upp ticker för '%s': %s", name, exc)
    return None


def get_current_price(symbol: str) -> tuple[float, str] | None:
    """Returnerar (pris, valuta) för en tickersymbol, t.ex. (342.5, 'USD')."""
    try:
        resp = requests.get(CHART_URL.format(symbol=symbol), headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice")
        currency = meta.get("currency", "")
        if price is None:
            return None
        return float(price), currency
    except Exception as exc:
        logger.warning("Kunde inte hämta pris för '%s': %s", symbol, exc)
    return None


def get_current_price_for_name(name: str, explicit_ticker: str | None = None) -> tuple[float, str, str] | None:
    """
    Slå upp pris för en aktie via namn (eller en explicit ticker om angiven).
    Returnerar (pris, valuta, ticker_som_användes) eller None om det misslyckas.
    """
    symbol = explicit_ticker or resolve_ticker(name)
    if not symbol:
        logger.warning("Kunde inte hitta någon ticker för '%s' — hoppar över den aktien.", name)
        return None
    result = get_current_price(symbol)
    if result is None:
        return None
    price, currency = result
    return price, currency, symbol
