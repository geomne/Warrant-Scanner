"""
Gemensam datamodell för alla källor.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Warrant:
    source: str                     # t.ex. "Avanza", "Nordnet", "Nasdaq Nordic", "Nordea Markets"
    name: str                       # produktnamn/beteckning, t.ex. "BULL VOLVO B X10 NDA"
    underlying: str                 # underliggande aktie, t.ex. "Volvo B"
    isin: Optional[str] = None
    direction: Optional[str] = None     # "Bull/Call" eller "Bear/Put"
    leverage: Optional[float] = None    # hävstång, t.ex. 10.0
    stop_loss: Optional[float] = None   # stop loss-nivå / knock-out-barriär
    last_price: Optional[float] = None  # senaste betalkurs
    currency: str = "SEK"
    issuer: Optional[str] = None        # emittent, t.ex. "Vontobel", "SEB", "Société Générale"
    url: Optional[str] = None           # länk till produktsidan
    updated_at: datetime = field(default_factory=datetime.now)

    def as_row(self) -> list:
        return [
            self.source,
            self.issuer or "-",
            self.name,
            self.direction or "-",
            f"{self.leverage:g}x" if self.leverage else "-",
            f"{self.stop_loss:g}" if self.stop_loss is not None else "-",
            f"{self.last_price:g}" if self.last_price is not None else "-",
            self.currency,
            self.updated_at.strftime("%H:%M:%S"),
        ]


ROW_HEADERS = [
    "Källa", "Emittent", "Namn", "Riktning", "Hävstång",
    "Stop loss", "Kurs", "Valuta", "Uppdaterad",
]
