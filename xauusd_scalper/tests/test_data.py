import pytest

from xauusd_scalper.data import generate_synthetic_data, load_ohlcv_csv


def test_generate_synthetic_data_shape_and_invariants():
    df = generate_synthetic_data(days=2, seed=1)
    assert len(df) > 0
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert (df["high"] >= df[["open", "close", "low"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close", "high"]].min(axis=1)).all()
    assert (df["close"] > 0).all()
    # No Saturday bars, and no Sunday bars before 22:00 UTC.
    assert not (df.index.weekday == 5).any()
    sunday = df.index.weekday == 6
    assert not (sunday & (df.index.hour < 22)).any()


def test_load_ohlcv_csv_roundtrip(tmp_path):
    df = generate_synthetic_data(days=1, seed=2)
    path = tmp_path / "sample.csv"
    df.to_csv(path)

    loaded = load_ohlcv_csv(str(path))
    assert len(loaded) == len(df)
    assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]


def test_load_ohlcv_csv_rejects_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("datetime,open,high,low\n2024-01-01,1,2,0.5\n")
    with pytest.raises(ValueError, match="missing required column"):
        load_ohlcv_csv(str(path))


def test_load_ohlcv_csv_rejects_bad_high(tmp_path):
    path = tmp_path / "bad_high.csv"
    path.write_text("datetime,open,high,low,close\n2024-01-01,10,9,8,9.5\n")
    with pytest.raises(ValueError, match="high is not the max"):
        load_ohlcv_csv(str(path))
