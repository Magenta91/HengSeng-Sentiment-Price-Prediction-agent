"""Maps free text to canonical asset symbols using configured aliases."""
from __future__ import annotations

import re
from dataclasses import dataclass


def _compile(alias: str) -> re.Pattern:
    alias = alias.strip()
    if alias.isascii():
        # Latin / numeric aliases need token boundaries ("AIA" must not match "MAIAMI", "0700" not "10700").
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(alias))  # CJK/Hangul are unspaced; substring matching is the norm


@dataclass
class TickerMatch:
    symbol: str
    alias: str


class TickerService:
    def __init__(self, assets):
        self._patterns: list[tuple[str, str, re.Pattern]] = []
        for asset in assets:
            for alias in {asset.symbol, asset.name, *asset.aliases}:
                if alias and alias.strip():
                    self._patterns.append((asset.symbol, alias, _compile(alias)))

    def match(self, text: str) -> list[TickerMatch]:
        """One match per symbol (the first alias that hits), in configuration order."""
        seen, out = set(), []
        for symbol, alias, pat in self._patterns:
            if symbol not in seen and pat.search(text or ""):
                seen.add(symbol)
                out.append(TickerMatch(symbol, alias))
        return out

    def symbols(self, text: str) -> list[str]:
        return [m.symbol for m in self.match(text)]
