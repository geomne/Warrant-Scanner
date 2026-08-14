#!/usr/bin/env python3
"""
Warrant Scanner
================
Söker igenom flera källor efter warranter/mini-futures.

LÄGE 1 — sök en eller flera aktier, se allt som finns:
    python main.py "Volvo B"
    python main.py "Volvo B" "Ericsson B" "Investor B"     # en tabell per aktie
    python main.py "Volvo B" --csv-dir resultat/
    python main.py "Volvo B" --interval 1800 --once --sort leverage --delay 3

LÄGE 2 — bevaka en lista med aktier, se bara LEVANDE warranter, i EN
gemensam lista sorterad efter vilka som är närmast sin stop loss:
    python main.py --watchlist watchlist.txt
    python main.py --watchlist watchlist.txt --csv bevakning.csv

Kör `python main.py --help` för alla flaggor.
"""
from __future__ import annotations
import argparse
import csv
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import NamedTuple

from config import SOURCES
from models import Warrant, ROW_HEADERS
from sources.base import SourceError
from sources.generic_engine import run_source
from pricing import get_current_price_for_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("warrant_scanner")

LONG_WORDS = {"lång", "long", "lang"}
SHORT_WORDS = {"kort", "short"}


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()


def collect(underlying: str, delay: float) -> list[Warrant]:
    """Kör alla källor för EN aktie, med en artighetspaus mellan varje källa."""
    all_warrants: list[Warrant] = []
    for i, cfg in enumerate(SOURCES):
        name = cfg["display_name"]
        logger.info("[%s] Söker i %s ...", underlying, name)
        try:
            results = run_source(cfg, underlying)
            logger.info("[%s] %s: %d träffar", underlying, name, len(results))
            all_warrants.extend(results)
        except SourceError as exc:
            logger.error("[%s] %s misslyckades: %s", underlying, name, exc)
        except Exception as exc:  # säkerhetsnät så en källa aldrig kraschar hela programmet
            logger.error("[%s] %s gav ett oväntat fel: %s", underlying, name, exc)

        if delay > 0 and i < len(SOURCES) - 1:
            time.sleep(delay)
    return all_warrants


def sort_warrants(warrants: list[Warrant], key: str) -> list[Warrant]:
    keyfuncs = {
        "leverage": lambda w: (w.leverage is None, -(w.leverage or 0)),
        "stop_loss": lambda w: (w.stop_loss is None, w.stop_loss or 0),
        "source": lambda w: w.source,
        "name": lambda w: w.name,
    }
    return sorted(warrants, key=keyfuncs.get(key, keyfuncs["leverage"]))


def print_table(warrants: list[Warrant], title: str) -> None:
    print(f"\n--- {title} ---")
    if not warrants:
        print("Inga warranter hittades. Se loggen ovan för fel per källa.")
        return
    try:
        from tabulate import tabulate
        rows = [w.as_row() for w in warrants]
        print(tabulate(rows, headers=ROW_HEADERS, tablefmt="simple"))
    except ImportError:
        widths = [max(len(h), 10) for h in ROW_HEADERS]
        print(" | ".join(h.ljust(w) for h, w in zip(ROW_HEADERS, widths)))
        for w in warrants:
            print(" | ".join(str(c).ljust(width) for c, width in zip(w.as_row(), widths)))
    print(f"Totalt {len(warrants)} warranter från {len({w.source for w in warrants})} källor.")


def write_csv(warrants: list[Warrant], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ROW_HEADERS + ["ISIN", "URL"])
        for w in warrants:
            writer.writerow(w.as_row() + [w.isin or "", w.url or ""])
    logger.info("CSV sparad: %s", path)


def run_once(underlyings: list[str], args) -> None:
    for underlying in underlyings:
        print(f"\n=== Warrant-sökning för '{underlying}' — {datetime.now():%Y-%m-%d %H:%M:%S} ===")
        warrants = collect(underlying, args.delay)
        warrants = sort_warrants(warrants, args.sort)
        print_table(warrants, underlying)

        if args.csv_dir:
            csv_path = os.path.join(args.csv_dir, f"{slugify(underlying)}.csv")
            write_csv(warrants, csv_path)
        elif args.csv and len(underlyings) == 1:
            write_csv(warrants, args.csv)

        # paus mellan aktier också, av samma artighetsskäl
        if args.delay > 0 and underlying is not underlyings[-1]:
            time.sleep(args.delay)


# ---------------------------------------------------------------------
# LÄGE 2: bevakningslista över flera aktier, filtrerad + sorterad efter
# avstånd till stop loss.
# ---------------------------------------------------------------------

class WatchEntry(NamedTuple):
    pct_to_stop: float
    stock: str
    current_price: float
    price_currency: str
    warrant: Warrant


WATCH_HEADERS = ["Avst. till SL %", "Aktie", "Aktiekurs", "Warrant", "Källa",
                  "Riktning", "Stop loss", "Hävstång", "Emittent"]


def read_watchlist_file(path: str) -> list[tuple[str, str | None]]:
    """Läser en textfil med en aktie per rad. Stöder 'Namn :: TICKER' för
    att manuellt ange ticker om den automatiska igenkänningen gissar fel.
    Tomma rader och rader som börjar med # ignoreras."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "::" in line:
                name, ticker = line.split("::", 1)
                entries.append((name.strip(), ticker.strip() or None))
            else:
                entries.append((line, None))
    return entries


def classify_direction(direction: str | None) -> str | None:
    if not direction:
        return None
    d = direction.strip().lower()
    if d in LONG_WORDS:
        return "long"
    if d in SHORT_WORDS:
        return "short"
    return None


def is_warrant_dead(direction_kind: str, current_price: float, stop_loss: float) -> bool:
    """Long (Lång/Bull): död om priset gått ner till/under stop loss.
    Short (Kort/Bear): död om priset gått upp till/över stop loss."""
    if direction_kind == "long":
        return current_price <= stop_loss
    else:  # "short"
        return current_price >= stop_loss


def run_watchlist(args) -> None:
    stock_entries = read_watchlist_file(args.watchlist)
    if not stock_entries:
        logger.error("Bevakningsfilen '%s' innehöll inga aktier.", args.watchlist)
        return

    watch_results: list[WatchEntry] = []
    dead_count = 0
    skipped_no_data_count = 0
    too_far_count = 0

    for i, (stock_name, explicit_ticker) in enumerate(stock_entries):
        print(f"\n=== Bevakning: {stock_name} — {datetime.now():%Y-%m-%d %H:%M:%S} ===")

        price_info = get_current_price_for_name(stock_name, explicit_ticker)
        if price_info is None:
            logger.error("[%s] Kunde inte hämta aktuellt aktiepris — hoppar över den här aktien "
                          "helt (ingen warrant kan filtreras/rankas utan ett pris).", stock_name)
            continue
        current_price, price_currency, ticker_used = price_info
        logger.info("[%s] Aktuellt pris: %.2f %s (ticker: %s)",
                     stock_name, current_price, price_currency, ticker_used)

        warrants = collect(stock_name, args.delay)
        logger.info("[%s] %d warranter hittade totalt (innan filtrering)", stock_name, len(warrants))

        for w in warrants:
            if w.stop_loss is None:
                skipped_no_data_count += 1
                continue
            direction_kind = classify_direction(w.direction)
            if direction_kind is None:
                logger.warning("[%s] %s: okänd/saknad riktning ('%s') — kan inte avgöra "
                                "död/levande, hoppar över.", stock_name, w.name, w.direction)
                skipped_no_data_count += 1
                continue

            if is_warrant_dead(direction_kind, current_price, w.stop_loss):
                dead_count += 1
                logger.info("[%s] Död warrant filtrerad bort: %s (kurs %.2f vs stop loss %.2f, %s)",
                            stock_name, w.name, current_price, w.stop_loss, w.direction)
                continue

            pct = abs(current_price - w.stop_loss) / current_price * 100
            if pct > args.max_distance_pct:
                too_far_count += 1
                continue
            watch_results.append(WatchEntry(
                pct_to_stop=pct, stock=stock_name, current_price=current_price,
                price_currency=price_currency, warrant=w,
            ))

        if args.delay > 0 and i < len(stock_entries) - 1:
            time.sleep(args.delay)

    watch_results.sort(key=lambda e: e.pct_to_stop)

    print(f"\n=== Bevakningslista — närmast stop loss först "
          f"({datetime.now():%Y-%m-%d %H:%M:%S}) ===")
    print(f"({dead_count} döda warranter filtrerade bort, {skipped_no_data_count} hoppade "
          f"över p.g.a. saknad stop loss/riktning, {too_far_count} filtrerade bort p.g.a. "
          f"mer än {args.max_distance_pct:g}% avstånd till stop loss)\n")

    if not watch_results:
        print("Inga levande warranter med tillräcklig data hittades. Se loggen ovan.")
        return

    rows = [[
        f"{e.pct_to_stop:.2f}%", e.stock, f"{e.current_price:g} {e.price_currency}",
        e.warrant.name, e.warrant.source, e.warrant.direction or "-",
        f"{e.warrant.stop_loss:g}" if e.warrant.stop_loss is not None else "-",
        f"{e.warrant.leverage:g}x" if e.warrant.leverage else "-",
        e.warrant.issuer or "-",
    ] for e in watch_results]

    try:
        from tabulate import tabulate
        print(tabulate(rows, headers=WATCH_HEADERS, tablefmt="simple"))
    except ImportError:
        widths = [max(len(h), 10) for h in WATCH_HEADERS]
        print(" | ".join(h.ljust(w) for h, w in zip(WATCH_HEADERS, widths)))
        for row in rows:
            print(" | ".join(str(c).ljust(width) for c, width in zip(row, widths)))

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(WATCH_HEADERS + ["ISIN", "URL"])
            for e, row in zip(watch_results, rows):
                writer.writerow(row + [e.warrant.isin or "", e.warrant.url or ""])
        logger.info("CSV sparad: %s", args.csv)


def main():
    parser = argparse.ArgumentParser(description="Sök warranter för en eller flera aktier över flera källor.")
    parser.add_argument("underlyings", nargs="*",
                         help="En eller flera aktier, t.ex. \"Volvo B\" \"Ericsson B\". "
                              "Utelämna och använd --watchlist istället för bevakningsläget.")
    parser.add_argument("--watchlist", metavar="FIL",
                         help="Textfil med en aktie per rad (se watchlist.txt för exempel). "
                              "Kör bevakningsläget: hämtar aktuellt pris per aktie, filtrerar "
                              "bort warranter vars stop loss redan passerats, och skriver ut "
                              "EN gemensam lista över alla aktier, sorterad efter vilka "
                              "warranter som ligger närmast sin stop loss just nu.")
    parser.add_argument("--interval", type=int, default=0,
                         help="Uppdatera var N:e sekund (t.ex. 1800 för 30 min). "
                              "Om 0 (standard) körs sökningen en gång och avslutas.")
    parser.add_argument("--once", action="store_true", help="Alias för --interval 0")
    parser.add_argument("--csv", metavar="FIL",
                         help="Spara resultat till en CSV-fil (vid vanligt läge: endast för EN aktie; "
                              "vid --watchlist: hela den sorterade listan)")
    parser.add_argument("--csv-dir", metavar="MAPP",
                         help="(Endast vanligt läge) Spara en CSV per aktie i angiven mapp")
    parser.add_argument("--sort", choices=["leverage", "stop_loss", "source", "name"],
                         default="leverage",
                         help="(Endast vanligt läge) Sorteringsordning (standard: hävstång, fallande)")
    parser.add_argument("--delay", type=float, default=2.0,
                         help="Paus i sekunder mellan varje käll-/aktieanrop, för att inte "
                              "belasta sajterna (standard: 2.0). Sätt till 0 för ingen paus.")
    parser.add_argument("--max-distance-pct", type=float, default=35.0,
                         help="(Endast --watchlist) Filtrera bort warranter vars stop loss "
                              "ligger mer än så här många procent från aktiens aktuella kurs "
                              "(standard: 35.0). Sätt högt (t.ex. 1000) för att inte filtrera alls.")
    args = parser.parse_args()

    if not args.watchlist and not args.underlyings:
        parser.error("ange antingen en eller flera aktier, eller --watchlist FIL")
    if args.watchlist and args.underlyings:
        parser.error("--watchlist kan inte kombineras med enskilda aktier på kommandoraden")
    if args.csv and args.underlyings and len(args.underlyings) > 1:
        logger.warning("--csv ignoreras vid flera aktier — använd --csv-dir istället.")

    interval = 0 if args.once else args.interval

    while True:
        if args.watchlist:
            run_watchlist(args)
        else:
            run_once(args.underlyings, args)
        if interval <= 0:
            break
        print(f"\nVäntar {interval} sekunder till nästa uppdatering (Ctrl+C för att avbryta) ...")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nAvbrutet av användaren.")
            sys.exit(0)


if __name__ == "__main__":
    main()
