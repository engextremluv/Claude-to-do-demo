"""Performance metrics computed from a completed simulation run."""

from __future__ import annotations

import math

import pandas as pd

from .engine import Trade


def compute_metrics(trades: list[Trade], equity_curve: pd.DataFrame, initial_balance: float) -> dict:
    total_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(t.pnl for t in losses)  # <= 0
    net_profit = gross_profit + gross_loss

    if equity_curve.empty:
        max_dd_pct = 0.0
        max_dd_abs = 0.0
    else:
        running_max = equity_curve["equity"].cummax()
        drawdown = equity_curve["equity"] - running_max
        drawdown_pct = drawdown / running_max.replace(0, pd.NA) * 100.0
        max_dd_abs = float(drawdown.min()) if len(drawdown) else 0.0
        max_dd_pct = float(drawdown_pct.min()) if drawdown_pct.notna().any() else 0.0

    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / total_trades * 100.0) if total_trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / abs(gross_loss)) if gross_loss != 0 else math.inf,
        "net_profit": net_profit,
        "return_pct": (net_profit / initial_balance * 100.0) if initial_balance else 0.0,
        "avg_win": (gross_profit / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "expectancy": (net_profit / total_trades) if total_trades else 0.0,
        "max_drawdown_abs": max_dd_abs,
        "max_drawdown_pct": max_dd_pct,
        "final_balance": initial_balance + net_profit,
    }


def format_report(metrics: dict, cfg_summary: str = "") -> str:
    lines = []
    if cfg_summary:
        lines.append(cfg_summary)
        lines.append("")
    lines.append("=== Backtest Summary ===")
    lines.append(f"Total trades:      {metrics['total_trades']}")
    lines.append(f"Win rate:          {metrics['win_rate_pct']:.2f}%  ({metrics['wins']}W / {metrics['losses']}L)")
    lines.append(f"Net profit:        ${metrics['net_profit']:.2f}")
    lines.append(f"Return:            {metrics['return_pct']:.2f}%")
    lines.append(f"Final balance:     ${metrics['final_balance']:.2f}")
    pf = metrics["profit_factor"]
    lines.append(f"Profit factor:     {'inf' if pf == math.inf else f'{pf:.2f}'}")
    lines.append(f"Avg win / loss:    ${metrics['avg_win']:.2f} / ${metrics['avg_loss']:.2f}")
    lines.append(f"Expectancy/trade:  ${metrics['expectancy']:.2f}")
    lines.append(f"Max drawdown:      ${metrics['max_drawdown_abs']:.2f}  ({metrics['max_drawdown_pct']:.2f}%)")
    return "\n".join(lines)
