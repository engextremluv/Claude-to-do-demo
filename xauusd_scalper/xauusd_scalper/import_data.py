"""Import bid/ask 1-min OHLCV feeds (e.g. yearly dumps like `{"ts","o","h","l","c",
"ao","ah","al","ac","v"}` — Unix-seconds timestamp, bid OHLC, ask OHLC, volume)
into the plain mid-price OHLCV CSV format the rest of this project consumes.

Also computes the actual spread observed in the data, since a real bid/ask
feed lets us calibrate RiskConfig.spread instead of guessing it.
"""

from __future__ import annotations

import gzip
import json

import pandas as pd

REQUIRED_KEYS = {"ts", "o", "h", "l", "c", "ao", "ah", "al", "ac"}


def _open_maybe_gzip(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def load_bid_ask_json(path: str) -> pd.DataFrame:
    """Read a bid/ask JSON (optionally gzip-compressed) feed into a DataFrame
    indexed by UTC datetime, with mid-price open/high/low/close/volume columns
    plus a `spread` column (ask_close - bid_close) per bar.
    """
    with _open_maybe_gzip(path) as fh:
        records = json.load(fh)

    if not isinstance(records, list) or not records:
        raise ValueError(f"{path} does not contain a non-empty JSON array of bars")
    missing = REQUIRED_KEYS - set(records[0].keys())
    if missing:
        raise ValueError(f"{path} records are missing expected key(s): {sorted(missing)}")

    df = pd.DataFrame.from_records(records)
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]

    out = pd.DataFrame(index=df.index)
    out["open"] = (df["o"] + df["ao"]) / 2.0
    out["high"] = (df["h"] + df["ah"]) / 2.0
    out["low"] = (df["l"] + df["al"]) / 2.0
    out["close"] = (df["c"] + df["ac"]) / 2.0
    out["volume"] = df["v"] if "v" in df.columns else 0.0
    out["spread"] = df["ac"] - df["c"]

    return out


def spread_stats(df: pd.DataFrame) -> dict:
    s = df["spread"]
    return {
        "mean": float(s.mean()),
        "median": float(s.median()),
        "min": float(s.min()),
        "max": float(s.max()),
        "p95": float(s.quantile(0.95)),
    }


def convert_to_ohlcv_csv(input_path: str, output_path: str) -> dict:
    """Convert a bid/ask JSON feed to this project's OHLCV CSV format.
    Returns a summary dict (bar count, date range, spread stats) for reporting.
    """
    df = load_bid_ask_json(input_path)
    df[["open", "high", "low", "close", "volume"]].to_csv(output_path)

    return {
        "bars": len(df),
        "start": df.index[0],
        "end": df.index[-1],
        "spread": spread_stats(df),
    }
