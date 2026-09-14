import numpy as np
import pandas as pd

from xauusd_scalper.indicators import atr, ema, rsi


def test_ema_converges_to_constant_series():
    s = pd.Series([100.0] * 50)
    result = ema(s, 10)
    assert np.isclose(result.iloc[-1], 100.0)


def test_ema_matches_pandas_ewm_reference():
    s = pd.Series(np.linspace(1900, 1950, 30))
    result = ema(s, 5)
    reference = s.ewm(span=5, adjust=False, min_periods=5).mean()
    pd.testing.assert_series_equal(result, reference)


def test_rsi_is_100_for_strictly_increasing_series():
    s = pd.Series(np.arange(1, 40, dtype=float))
    result = rsi(s, 14)
    assert np.isclose(result.iloc[-1], 100.0)


def test_rsi_is_0_for_strictly_decreasing_series():
    s = pd.Series(np.arange(40, 1, -1, dtype=float))
    result = rsi(s, 14)
    assert np.isclose(result.iloc[-1], 0.0)


def test_rsi_bounded_0_100():
    rng = np.random.default_rng(0)
    s = pd.Series(1950 + np.cumsum(rng.normal(0, 0.5, 200)))
    result = rsi(s, 14).dropna()
    assert (result >= 0).all() and (result <= 100).all()


def test_atr_nonnegative_and_zero_for_flat_bars():
    df = pd.DataFrame({"high": [10.0] * 20, "low": [10.0] * 20, "close": [10.0] * 20})
    result = atr(df, 14)
    assert (result.dropna() == 0).all()


def test_atr_positive_for_volatile_bars():
    df = pd.DataFrame(
        {
            "high": [10.0, 11.0, 9.0, 12.0, 8.0] * 5,
            "low": [9.0, 9.5, 8.0, 9.0, 7.0] * 5,
            "close": [9.5, 10.5, 8.5, 10.0, 7.5] * 5,
        }
    )
    result = atr(df, 14)
    assert (result.dropna() > 0).all()
