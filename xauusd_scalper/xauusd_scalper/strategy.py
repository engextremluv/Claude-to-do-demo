"""EMA-crossover / RSI-filtered 1-minute scalping strategy for XAUUSD.

Entry logic (evaluated on each closed bar):
  - Long when the fast EMA crosses above the slow EMA while RSI confirms
    bullish momentum without being overbought (rsi_bull_min..rsi_bull_max).
  - Short when the fast EMA crosses below the slow EMA while RSI confirms
    bearish momentum without being oversold (rsi_bear_min..rsi_bear_max).
  - Optionally restricted to the London/New York session window, since gold
    spreads widen outside it enough to erode a scalp's edge.

A signal computed from bar N's close is only ever acted on starting at bar
N+1 (the simulation engine enforces this) so there is no lookahead bias.
Stop-loss/take-profit distances are ATR-based, sized at the signal bar.
"""

from __future__ import annotations

import pandas as pd

from .config import StrategyConfig
from .indicators import atr, ema, rsi


def generate_signals(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Return a copy of df with indicator columns, a `signal` column (1/-1/0),
    and `sl_dist`/`tp_dist` price-distance columns for bars carrying a signal.
    """
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.fast_ema)
    out["ema_slow"] = ema(out["close"], cfg.slow_ema)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)

    fast, slow = out["ema_fast"], out["ema_slow"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))

    bull_ok = out["rsi"].between(cfg.rsi_bull_min, cfg.rsi_bull_max)
    bear_ok = out["rsi"].between(cfg.rsi_bear_min, cfg.rsi_bear_max)

    if cfg.use_session_filter:
        hour = out.index.hour
        if cfg.session_start_hour <= cfg.session_end_hour:
            in_session = (hour >= cfg.session_start_hour) & (hour < cfg.session_end_hour)
        else:
            # Handle a window that wraps past midnight UTC.
            in_session = (hour >= cfg.session_start_hour) | (hour < cfg.session_end_hour)
        in_session = pd.Series(in_session, index=out.index)
    else:
        in_session = pd.Series(True, index=out.index)

    has_indicators = out["ema_fast"].notna() & out["ema_slow"].notna() & out["rsi"].notna() & out["atr"].notna()

    long_signal = cross_up & bull_ok & in_session & has_indicators
    short_signal = cross_down & bear_ok & in_session & has_indicators

    out["signal"] = 0
    out.loc[long_signal, "signal"] = 1
    out.loc[short_signal, "signal"] = -1

    out["sl_dist"] = pd.NA
    out["tp_dist"] = pd.NA
    signaled = out["signal"] != 0
    out.loc[signaled, "sl_dist"] = out.loc[signaled, "atr"] * cfg.atr_sl_mult
    out.loc[signaled, "tp_dist"] = out.loc[signaled, "atr"] * cfg.atr_tp_mult

    return out
