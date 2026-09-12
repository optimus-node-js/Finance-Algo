"""
Pluggable strategy framework.

Every strategy takes today's bulk Quote map + a baseline map (per-symbol
20-day avg volume / prev close) and returns a list of Signal objects. This
is deliberately generic so more indicators (RSI, VWAP breakout, SMA/EMA
crossover, ADX momentum, etc.) can be added as new Strategy subclasses
without touching the scanner/execution code — register them in STRATEGIES
and they become selectable.

Only one strategy ships fully wired today: GapVolumeShockStrategy, which is
the existing "Institutional Footprint" rule from Trading/Stocks/main.ipynb
(gap up >= MIN_GAP_PERCENT, volume > VOL_SHOCK_MULT x 20-day avg). A second
helper, rank_momentum(), is included because Tadka specifically asked for
"find the best equity with higher/down momentum" — it's a ranking utility
(not an auto-trading strategy yet) that any future strategy or the API can
call directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.market_data import Quote


@dataclass
class Signal:
    symbol: str
    token: str
    strategy_name: str
    price: float
    technical_data: dict


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def scan(self, quotes: dict[str, Quote], baselines: dict[str, dict]) -> list[Signal]:
        ...


class GapVolumeShockStrategy(Strategy):
    """Gap up + volume shock, long only. See Trading/Stocks/main.ipynb for the
    original research and the walk-forward validation notes in project memory
    (win rate ~58%, stable across time; DO NOT reuse the notebook's compounding
    position-sizing -- that's what produced the misleading +23,000% backtest)."""

    name = "GapVolShock_v1"

    def __init__(self, min_gap_percent: float = 1.5, vol_shock_mult: float = 3.0):
        self.min_gap_percent = min_gap_percent
        self.vol_shock_mult = vol_shock_mult

    def scan(self, quotes: dict[str, Quote], baselines: dict[str, dict]) -> list[Signal]:
        signals = []
        for symbol, quote in quotes.items():
            base = baselines.get(symbol)
            if not base or not base.get("avg_vol"):
                continue
            gap_pct = ((quote.open - base["prev_close"]) / base["prev_close"] * 100
                       if base["prev_close"] else 0)
            vol_mult = quote.volume / base["avg_vol"] if base["avg_vol"] else 0

            if gap_pct > self.min_gap_percent and vol_mult > self.vol_shock_mult:
                signals.append(Signal(
                    symbol=symbol,
                    token=quote.token,
                    strategy_name=self.name,
                    price=quote.ltp,
                    technical_data={
                        "gap_pct": round(gap_pct, 2),
                        "vol_mult": round(vol_mult, 2),
                        "avg_vol": base["avg_vol"],
                        "prev_close": base["prev_close"],
                    },
                ))
        return signals


STRATEGIES: dict[str, Strategy] = {
    GapVolumeShockStrategy.name: GapVolumeShockStrategy(),
}


def rank_momentum(quotes: dict[str, Quote], top_n: int = 10) -> dict[str, list[dict]]:
    """Ranks today's universe by %-change-from-prev-close. Returns the top N
    up-momentum and top N down-momentum names. Pure ranking utility -- callers
    (API endpoint or a future strategy) decide what to do with it."""
    rows = []
    for symbol, q in quotes.items():
        if not q.close:
            continue
        pct = (q.ltp - q.close) / q.close * 100
        rows.append({"symbol": symbol, "ltp": q.ltp, "prev_close": q.close, "change_pct": round(pct, 2)})
    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    return {"top_up_momentum": rows[:top_n], "top_down_momentum": rows[-top_n:][::-1]}
