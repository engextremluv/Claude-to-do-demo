import numpy as np
import pandas as pd

from xauusd_scalper.config import StrategyConfig
from xauusd_scalper.data import generate_synthetic_data
from xauusd_scalper.strategy import generate_signals


def test_generate_signals_adds_expected_columns():
    df = generate_synthetic_data(days=2, seed=3)
    out = generate_signals(df, StrategyConfig())
    for col in ["ema_fast", "ema_slow", "rsi", "atr", "signal", "sl_dist", "tp_dist"]:
        assert col in out.columns
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_signals_only_fire_within_session_when_filter_enabled():
    df = generate_synthetic_data(days=3, seed=4)
    cfg = StrategyConfig(use_session_filter=True, session_start_hour=7, session_end_hour=16)
    out = generate_signals(df, cfg)
    signaled_hours = out.index[out["signal"] != 0].hour
    assert signaled_hours.isin(range(7, 16)).all()


def test_no_session_filter_allows_signals_outside_window():
    df = generate_synthetic_data(days=3, seed=4)
    cfg_filtered = StrategyConfig(use_session_filter=True)
    cfg_open = StrategyConfig(use_session_filter=False)
    out_filtered = generate_signals(df, cfg_filtered)
    out_open = generate_signals(df, cfg_open)
    # Disabling the filter should never produce fewer signal bars than keeping it on.
    assert (out_open["signal"] != 0).sum() >= (out_filtered["signal"] != 0).sum()


def test_sl_tp_distances_only_set_on_signal_bars():
    df = generate_synthetic_data(days=2, seed=5)
    out = generate_signals(df, StrategyConfig())
    no_signal = out["signal"] == 0
    assert out.loc[no_signal, "sl_dist"].isna().all()
    assert out.loc[no_signal, "tp_dist"].isna().all()
    has_signal = out["signal"] != 0
    if has_signal.any():
        assert out.loc[has_signal, "sl_dist"].notna().all()
        assert out.loc[has_signal, "tp_dist"].notna().all()
        assert (out.loc[has_signal, "sl_dist"] > 0).all()
        assert (out.loc[has_signal, "tp_dist"] > 0).all()


def test_ema_crossover_detected_without_rsi_or_session_filter():
    # Handcrafted price path: flat, then a sharp rally forces a bullish
    # fast/slow EMA crossover partway through.
    n = 60
    prices = np.concatenate([np.full(20, 1950.0), 1950.0 + np.cumsum(np.full(n - 20, 0.8))])
    idx = pd.date_range("2024-01-01 08:00", periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.1,
            "low": prices - 0.1,
            "close": prices,
            "volume": 100.0,
        },
        index=idx,
    )
    cfg = StrategyConfig(
        fast_ema=3,
        slow_ema=8,
        rsi_bull_min=0,
        rsi_bull_max=100,
        use_session_filter=False,
    )
    out = generate_signals(df, cfg)
    assert (out["signal"] == 1).any()
