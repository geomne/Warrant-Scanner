"""
Gemensamma HTTP-hjälpfunktioner för alla källmoduler.
"""
import time
import logging
import requests

logger = logging.getLogger("warrant_scanner")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
}


class SourceError(Exception):
    """Höjs när en källa inte kan hämtas/tolkas."""


def fetch(url: str, *, params: dict | None = None, headers: dict | None = None,
          timeout: int = 15, retries: int = 2, backoff: float = 1.5) -> requests.Response:
    """GET med retries. Kastar SourceError om alla försök misslyckas."""
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            resp = requests.get(url, params=params, headers=merged_headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Försök %s/%s mot %s misslyckades: %s", attempt, retries + 1, url, exc)
            if attempt <= retries:
                time.sleep(backoff * attempt)
    raise SourceError(f"Kunde inte hämta {url}: {last_exc}")


def fetch_json(url: str, **kwargs) -> dict:
    resp = fetch(url, **kwargs)
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"Svaret från {url} var inte giltig JSON: {exc}") from exc
