"""Command-line entrypoint: generate-sample / backtest / paper subcommands.

Usage (from the xauusd_scalper/ project directory):
    python -m xauusd_scalper.cli generate-sample --out sample_data/xauusd_1min_sample.csv
    python -m xauusd_scalper.cli backtest --data sample_data/xauusd_1min_sample.csv
    python -m xauusd_scalper.cli paper --data sample_data/xauusd_1min_sample.csv --speed 0
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from .backtest import format_report, run_backtest
from .config import RiskConfig, StrategyConfig, load_config
from .data import generate_synthetic_data, load_ohlcv_csv
from .paper import run_paper_trading


def _add_strategy_risk_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="JSON file with {'strategy': {...}, 'risk': {...}} overrides")
    parser.add_argument("--fast-ema", type=int, help="Fast EMA period")
    parser.add_argument("--slow-ema", type=int, help="Slow EMA period")
    parser.add_argument("--rsi-period", type=int, help="RSI period")
    parser.add_argument("--atr-sl-mult", type=float, help="Stop-loss distance = ATR * this multiple")
    parser.add_argument("--atr-tp-mult", type=float, help="Take-profit distance = ATR * this multiple")
    parser.add_argument("--no-session-filter", action="store_true", help="Trade all hours, not just the London/NY session")
    parser.add_argument("--balance", type=float, help="Initial account balance (USD)")
    parser.add_argument("--risk-pct", type=float, help="Percent of balance risked per trade")
    parser.add_argument("--spread", type=float, help="XAUUSD spread in USD/oz")


def _build_configs(args: argparse.Namespace) -> tuple[StrategyConfig, RiskConfig]:
    if args.config:
        strategy_cfg, risk_cfg = load_config(args.config)
    else:
        strategy_cfg, risk_cfg = StrategyConfig(), RiskConfig()

    overrides_strategy = {}
    if args.fast_ema is not None:
        overrides_strategy["fast_ema"] = args.fast_ema
    if args.slow_ema is not None:
        overrides_strategy["slow_ema"] = args.slow_ema
    if args.rsi_period is not None:
        overrides_strategy["rsi_period"] = args.rsi_period
    if args.atr_sl_mult is not None:
        overrides_strategy["atr_sl_mult"] = args.atr_sl_mult
    if args.atr_tp_mult is not None:
        overrides_strategy["atr_tp_mult"] = args.atr_tp_mult
    if args.no_session_filter:
        overrides_strategy["use_session_filter"] = False
    if overrides_strategy:
        strategy_cfg = dataclasses.replace(strategy_cfg, **overrides_strategy)

    overrides_risk = {}
    if args.balance is not None:
        overrides_risk["initial_balance"] = args.balance
    if args.risk_pct is not None:
        overrides_risk["risk_per_trade_pct"] = args.risk_pct
    if args.spread is not None:
        overrides_risk["spread"] = args.spread
    if overrides_risk:
        risk_cfg = dataclasses.replace(risk_cfg, **overrides_risk)

    return strategy_cfg, risk_cfg


def cmd_generate_sample(args: argparse.Namespace) -> int:
    df = generate_synthetic_data(start=args.start, days=args.days, start_price=args.start_price, seed=args.seed)
    df.to_csv(args.out)
    print(f"Wrote {len(df)} synthetic 1-min bars to {args.out}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    strategy_cfg, risk_cfg = _build_configs(args)
    df = load_ohlcv_csv(args.data)
    result = run_backtest(df, strategy_cfg, risk_cfg)
    result.save(args.output_dir)
    print(format_report(result.metrics))
    print(f"\nWrote trades.csv and equity_curve.csv to {args.output_dir}")
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    strategy_cfg, risk_cfg = _build_configs(args)
    df = load_ohlcv_csv(args.data)
    run_paper_trading(df, strategy_cfg, risk_cfg, args.output_dir, speed=args.speed, heartbeat_every=args.heartbeat_every)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xauusd_scalper", description="XAUUSD 1-min EMA/RSI scalping bot (backtest + paper trading only)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate-sample", help="Generate a synthetic 1-min XAUUSD CSV for testing")
    p_gen.add_argument("--out", default="sample_data/xauusd_1min_sample.csv")
    p_gen.add_argument("--start", default="2024-01-01 00:00:00")
    p_gen.add_argument("--days", type=int, default=5)
    p_gen.add_argument("--start-price", type=float, default=1950.0)
    p_gen.add_argument("--seed", type=int, default=42)
    p_gen.set_defaults(func=cmd_generate_sample)

    p_bt = sub.add_parser("backtest", help="Run a full-history backtest over a CSV of 1-min candles")
    p_bt.add_argument("--data", required=True, help="Path to OHLCV CSV")
    p_bt.add_argument("--output-dir", default="output/backtest")
    _add_strategy_risk_args(p_bt)
    p_bt.set_defaults(func=cmd_backtest)

    p_paper = sub.add_parser("paper", help="Paper-trade by streaming a CSV of 1-min candles bar by bar")
    p_paper.add_argument("--data", required=True, help="Path to OHLCV CSV")
    p_paper.add_argument("--output-dir", default="output/paper")
    p_paper.add_argument("--speed", type=float, default=0.0, help="Seconds to sleep between bars (0 = as fast as possible)")
    p_paper.add_argument("--heartbeat-every", type=int, default=60, help="Print a status line every N bars with no trade event")
    _add_strategy_risk_args(p_paper)
    p_paper.set_defaults(func=cmd_paper)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
