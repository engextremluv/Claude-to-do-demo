import pandas as pd
import pytest

from xauusd_scalper.config import RiskConfig
from xauusd_scalper.engine import simulate
from xauusd_scalper.risk import calc_position_size


def _risk_cfg(**overrides):
    base = dict(
        initial_balance=10_000.0,
        risk_per_trade_pct=1.0,
        contract_size=100.0,
        min_lot=0.01,
        max_lot=5.0,
        lot_step=0.01,
        spread=0.2,
        commission_per_lot=7.0,
        max_open_positions=1,
    )
    base.update(overrides)
    return RiskConfig(**base)


def _make_df(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1min", tz="UTC")
    df = pd.DataFrame(rows, index=idx)
    return df


def test_long_trade_executes_next_bar_and_hits_take_profit():
    risk_cfg = _risk_cfg()
    rows = [
        dict(open=2000.0, high=2000.5, low=1999.5, close=2000.2, signal=1, sl_dist=1.0, tp_dist=2.0),
        dict(open=2000.3, high=2000.6, low=1999.9, close=2000.4, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
        dict(open=2000.5, high=2002.6, low=2000.0, close=2002.5, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
        dict(open=2002.4, high=2002.7, low=2002.0, close=2002.3, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
    ]
    df = _make_df(rows)

    events = list(simulate(df, risk_cfg))

    # No trade opens on bar 0 itself (signal decided at close, executed at bar 1's open).
    assert events[0]["opened"] is None
    assert events[1]["opened"] is not None
    trade = events[1]["opened"]
    half_spread = risk_cfg.spread / 2.0
    assert trade.entry_price == pytest.approx(2000.3 + half_spread)
    assert trade.sl_price == pytest.approx(trade.entry_price - 1.0)
    assert trade.tp_price == pytest.approx(trade.entry_price + 2.0)

    lots = calc_position_size(risk_cfg.initial_balance, 1.0, risk_cfg)
    assert trade.lots == pytest.approx(lots)

    # TP hit on bar 2 (high 2002.6 >= tp_price).
    assert events[2]["closed"] is not None
    closed = events[2]["closed"]
    assert closed.exit_reason == "tp"
    expected_exit = trade.tp_price - half_spread
    assert closed.exit_price == pytest.approx(expected_exit)
    expected_pnl = 1 * (expected_exit - trade.entry_price) * trade.lots * risk_cfg.contract_size
    expected_pnl -= risk_cfg.commission_per_lot * trade.lots
    assert closed.pnl == pytest.approx(expected_pnl)
    assert events[2]["balance"] == pytest.approx(risk_cfg.initial_balance + expected_pnl)

    # Flat afterwards, no more trades.
    assert events[3]["opened"] is None
    assert events[3]["closed"] is None


def test_stop_loss_and_take_profit_in_same_bar_assumes_stop_loss_hit_first():
    risk_cfg = _risk_cfg()
    rows = [
        dict(open=2000.0, high=2000.1, low=1999.9, close=2000.0, signal=1, sl_dist=1.0, tp_dist=1.0),
        dict(open=2000.1, high=2001.5, low=1998.5, close=2000.0, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
    ]
    df = _make_df(rows)
    events = list(simulate(df, risk_cfg))
    closed = events[1]["closed"]
    assert closed is not None
    assert closed.exit_reason == "sl"


def test_open_position_force_closed_at_end_of_data():
    risk_cfg = _risk_cfg()
    rows = [
        dict(open=2000.0, high=2000.1, low=1999.9, close=2000.0, signal=1, sl_dist=5.0, tp_dist=5.0),
        dict(open=2000.1, high=2000.2, low=2000.0, close=2000.1, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
    ]
    df = _make_df(rows)
    events = list(simulate(df, risk_cfg))
    last = events[-1]
    assert last["closed"] is not None
    assert last["closed"].exit_reason == "eod"
    assert last["position"] is None


def test_no_new_entry_while_position_open():
    risk_cfg = _risk_cfg()
    rows = [
        dict(open=2000.0, high=2000.1, low=1999.9, close=2000.0, signal=1, sl_dist=10.0, tp_dist=10.0),
        dict(open=2000.1, high=2000.2, low=2000.0, close=2000.1, signal=-1, sl_dist=10.0, tp_dist=10.0),
        dict(open=2000.1, high=2000.2, low=2000.0, close=2000.1, signal=0, sl_dist=pd.NA, tp_dist=pd.NA),
    ]
    df = _make_df(rows)
    events = list(simulate(df, risk_cfg))
    # The short signal queued on bar 1 is ignored because a position is already open.
    assert events[2]["opened"] is None
    assert events[1]["position"] is not None
    assert events[1]["position"].direction == 1


def test_calc_position_size_respects_min_and_max_lot():
    risk_cfg = _risk_cfg(risk_per_trade_pct=0.001, min_lot=0.01, max_lot=5.0)
    lots = calc_position_size(10_000.0, 1.0, risk_cfg)
    assert lots == pytest.approx(risk_cfg.min_lot)

    risk_cfg2 = _risk_cfg(risk_per_trade_pct=1000.0, min_lot=0.01, max_lot=5.0)
    lots2 = calc_position_size(10_000.0, 0.01, risk_cfg2)
    assert lots2 == pytest.approx(risk_cfg2.max_lot)


def test_calc_position_size_zero_stop_distance_returns_zero():
    risk_cfg = _risk_cfg()
    assert calc_position_size(10_000.0, 0.0, risk_cfg) == 0.0
