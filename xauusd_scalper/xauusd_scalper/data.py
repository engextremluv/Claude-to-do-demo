"""OHLCV data loading and synthetic sample-data generation.

No live/broker data feed is wired up here by design (this project runs in
backtest + paper-trading mode only) — candles come from CSV files you supply.
`generate_synthetic_data` exists purely so the bot is runnable end-to-end
without needing real XAUUSD history first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close"]

# Accepts common header spellings and normalizes them to our canonical names.
_COLUMN_ALIASES = {
    "timestamp": "datetime",
    "time": "datetime",
    "date": "datetime",
    "datetime": "datetime",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "vol": "volume",
}


def load_ohlcv_csv(path: str) -> pd.DataFrame:
    """Load a 1-minute OHLCV CSV into a clean, sorted DataFrame indexed by UTC datetime.

    Expects (case-insensitively) a datetime-like column (datetime/timestamp/date/time)
    and open/high/low/close columns; volume is optional.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    rename = {}
    for col in df.columns:
        if col in _COLUMN_ALIASES:
            rename[col] = _COLUMN_ALIASES[col]
    df = df.rename(columns=rename)

    if "datetime" not in df.columns:
        raise ValueError(
            "CSV must have a datetime column (accepted names: datetime, timestamp, date, time)"
        )
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required column(s): {missing}")

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[["open", "high", "low", "close", "volume"]].astype(float)

    if df[REQUIRED_COLUMNS].isna().any().any():
        raise ValueError("CSV contains NaN values in open/high/low/close")
    if not (df["high"] >= df[["open", "close", "low"]].max(axis=1)).all():
        raise ValueError("CSV has rows where high is not the max of open/high/low/close")
    if not (df["low"] <= df[["open", "close", "high"]].min(axis=1)).all():
        raise ValueError("CSV has rows where low is not the min of open/high/low/close")
    if df.empty:
        raise ValueError("CSV contains no rows")

    return df


def generate_synthetic_data(
    start: str = "2024-01-01 00:00:00",
    days: int = 5,
    start_price: float = 1950.0,
    seed: int | None = 42,
) -> pd.DataFrame:
    """Generate synthetic 1-minute XAUUSD-like OHLCV bars for testing/demoing the bot.

    This is NOT real market data — it's a bounded random walk with regime shifts
    and session-dependent volatility, only meant to exercise the strategy/engine
    end-to-end before you plug in real historical data.
    """
    rng = np.random.default_rng(seed)

    start_ts = pd.Timestamp(start, tz="UTC")
    all_minutes = pd.date_range(start_ts, periods=days * 24 * 60, freq="1min", tz="UTC")
    # Gold trades ~23h/day Sun evening to Fri evening; approximate that by
    # dropping Saturdays and Sunday-before-22:00-UTC from the synthetic feed.
    keep = ~(
        (all_minutes.weekday == 5)
        | ((all_minutes.weekday == 6) & (all_minutes.hour < 22))
    )
    minutes = all_minutes[keep]
    n = len(minutes)

    hours = minutes.hour.to_numpy()
    # Higher volatility during the London/New York overlap, lower overnight.
    session_vol = np.where((hours >= 7) & (hours < 16), 1.0, 0.4)

    # Regime-shifting drift so EMA crossovers actually occur.
    regime_len = 120  # minutes per drift regime
    n_regimes = n // regime_len + 2
    regime_drift = rng.normal(loc=0.0, scale=0.015, size=n_regimes)
    drift_per_bar = np.repeat(regime_drift, regime_len)[:n]

    base_vol = 0.12  # USD, 1-sigma per-bar noise at full session volatility
    noise = rng.normal(loc=0.0, scale=base_vol, size=n) * session_vol
    step = drift_per_bar * session_vol + noise

    close = start_price + np.cumsum(step)
    close = np.clip(close, 1.0, None)  # keep price positive under any seed

    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]

    intrabar_range = np.abs(rng.normal(loc=0.0, scale=base_vol * 0.8, size=n)) * session_vol + 0.01
    high = np.maximum(open_, close) + intrabar_range * rng.uniform(0.1, 1.0, size=n)
    low = np.minimum(open_, close) - intrabar_range * rng.uniform(0.1, 1.0, size=n)
    volume = rng.integers(50, 500, size=n).astype(float)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=minutes,
    )
    df.index.name = "datetime"
    return df
