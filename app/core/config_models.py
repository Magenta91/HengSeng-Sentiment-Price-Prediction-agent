"""Exchange / asset / source configuration with explicit validation (framework-free)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

from app.core.exceptions import ConfigError
from app.ml.trading_calendar import TradingCalendar


@dataclass(frozen=True)
class AssetConfig:
    symbol: str
    name: str
    aliases: tuple = ()
    search_terms: tuple = ()

    def queries(self) -> list[str]:
        return list(self.search_terms or self.aliases[:3] or (self.name,))


@dataclass(frozen=True)
class SourceConfig:
    name: str
    kind: str = "apify"
    enabled: bool = False
    actor_id: str | None = None
    language: str | None = None
    source_timezone: str = "UTC"
    max_items: int = 500
    input_template: dict = field(default_factory=dict)
    field_map: dict = field(default_factory=dict)
    notes: str = ""

    def operational_problem(self) -> str | None:
        """Why this source cannot run, or None if it is fully configured."""
        if not self.enabled:
            return "disabled in configuration"
        if not self.actor_id:
            return "no Apify actor_id configured (select and test an actor first)"
        
        # Check if field_map uses nested structure (thread/reply) or flat structure
        has_thread = "thread" in self.field_map and isinstance(self.field_map["thread"], dict)
        has_reply = "reply" in self.field_map and isinstance(self.field_map["reply"], dict)
        
        if has_thread or has_reply:
            # Nested structure validation (e.g., LIHKG with thread and reply records)
            if has_thread:
                thread_map = self.field_map["thread"]
                if not thread_map.get("text") and not thread_map.get("own_text"):
                    return "field_map.thread.text not configured (inspect the actor's dataset output)"
                if not thread_map.get("timestamp"):
                    return "field_map.thread.timestamp not configured"
            if has_reply:
                reply_map = self.field_map["reply"]
                if not reply_map.get("text") and not reply_map.get("own_text"):
                    return "field_map.reply.text not configured"
                if not reply_map.get("timestamp"):
                    return "field_map.reply.timestamp not configured"
        else:
            # Flat structure validation (traditional social adapters)
            if not self.field_map.get("text"):
                return "field_map.text not configured (inspect the actor's dataset output)"
            if not self.field_map.get("timestamp"):
                return "field_map.timestamp not configured"
        
        return None


@dataclass(frozen=True)
class ExchangeConfig:
    code: str
    name: str
    timezone: str
    currency: str
    close_time: time
    assets: tuple
    sources: tuple
    holidays: frozenset = frozenset()

    def calendar(self) -> TradingCalendar:
        return TradingCalendar(self.code, self.timezone, self.close_time, self.holidays)

    def asset(self, symbol: str) -> AssetConfig:
        for a in self.assets:
            if a.symbol == symbol:
                return a
        raise KeyError(symbol)

    def scope_warnings(self) -> list[str]:
        out = []
        if len(self.assets) != 5:
            out.append(f"{self.code}: assignment expects 5 assets, {len(self.assets)} configured")
        if len(self.sources) < 2:
            out.append(f"{self.code}: assignment expects >= 2 social sources, {len(self.sources)} configured")
        return out


@dataclass(frozen=True)
class ExchangesConfig:
    exchanges: dict

    def get(self, code: str) -> ExchangeConfig:
        try:
            return self.exchanges[code]
        except KeyError:
            raise ConfigError(f"unknown exchange '{code}'; configured: {sorted(self.exchanges)}") from None

    @classmethod
    def from_dict(cls, data: dict) -> ExchangesConfig:
        exchanges = {}
        for raw in data.get("exchanges", []):
            try:
                hh, mm = str(raw["close_time"]).split(":")
                assets = tuple(AssetConfig(symbol=a["symbol"], name=a["name"],
                                           aliases=tuple(a.get("aliases", ())),
                                           search_terms=tuple(a.get("search_terms", ())))
                               for a in raw["assets"])
                sources = tuple(SourceConfig(
                    name=s["name"], kind=s.get("kind", "apify"), enabled=bool(s.get("enabled", False)),
                    actor_id=s.get("actor_id"), language=s.get("language"),
                    source_timezone=s.get("source_timezone", "UTC"), max_items=int(s.get("max_items", 500)),
                    input_template=dict(s.get("input_template", {})), field_map=dict(s.get("field_map", {})),
                    notes=s.get("notes", "")) for s in raw.get("sources", []))
                ex = ExchangeConfig(
                    code=raw["code"], name=raw["name"], timezone=raw["timezone"], currency=raw["currency"],
                    close_time=time(int(hh), int(mm)), assets=assets, sources=sources,
                    holidays=frozenset(date.fromisoformat(d) for d in raw.get("holidays", [])))
                ex.calendar().tz  # validates the timezone name
            except (KeyError, ValueError, TypeError) as exc:
                raise ConfigError(f"invalid exchange config {raw.get('code', '?')}: {exc!r}") from exc
            if not ex.assets:
                raise ConfigError(f"exchange {ex.code} has no assets")
            if len({a.symbol for a in ex.assets}) != len(ex.assets):
                raise ConfigError(f"exchange {ex.code} has duplicate asset symbols")
            exchanges[ex.code] = ex
        if not exchanges:
            raise ConfigError("no exchanges configured")
        return cls(exchanges)

    @classmethod
    def load(cls, path: str | Path) -> ExchangesConfig:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"exchange config not found: {p}")
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
