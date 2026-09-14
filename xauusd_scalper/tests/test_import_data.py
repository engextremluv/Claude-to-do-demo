import gzip
import json

from xauusd_scalper.import_data import convert_to_ohlcv_csv, load_bid_ask_json
from xauusd_scalper.data import load_ohlcv_csv


def _write_sample(path, gz=False):
    records = [
        {"ts": 1735772400, "o": 2625.1, "h": 2626.0, "l": 2624.3, "c": 2625.0, "ao": 2625.5, "ah": 2626.4, "al": 2624.7, "ac": 2625.4, "v": 0.07},
        {"ts": 1735772460, "o": 2625.0, "h": 2625.2, "l": 2624.4, "c": 2624.9, "ao": 2625.4, "ah": 2625.6, "al": 2624.8, "ac": 2625.3, "v": 0.02},
    ]
    opener = gzip.open if gz else open
    mode = "wt" if gz else "w"
    with opener(path, mode, encoding="utf-8") as fh:
        json.dump(records, fh)
    return records


def test_load_bid_ask_json_computes_mid_prices(tmp_path):
    path = tmp_path / "feed.json"
    _write_sample(path)
    df = load_bid_ask_json(str(path))
    assert len(df) == 2
    assert df["open"].iloc[0] == (2625.1 + 2625.5) / 2
    assert df["spread"].iloc[0] == 2625.4 - 2625.0


def test_load_bid_ask_json_handles_gzip(tmp_path):
    path = tmp_path / "feed.json.gz"
    _write_sample(path, gz=True)
    df = load_bid_ask_json(str(path))
    assert len(df) == 2


def test_convert_to_ohlcv_csv_produces_loadable_csv(tmp_path):
    src = tmp_path / "feed.json"
    _write_sample(src)
    out = tmp_path / "converted.csv"
    summary = convert_to_ohlcv_csv(str(src), str(out))
    assert summary["bars"] == 2
    loaded = load_ohlcv_csv(str(out))
    assert len(loaded) == 2
    assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]
