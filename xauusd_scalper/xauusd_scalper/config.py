"""Strategy and risk-management configuration for the XAUUSD scalper."""

from dataclasses import dataclass, asdict
import json


@dataclass
class StrategyConfig:
    """Parameters for the EMA-crossover / RSI-filtered scalping strategy."""

    fast_ema: int = 9
    slow_ema: int = 21
    rsi_period: int = 14
    # Only take longs while RSI sits in this band (trend-confirming, not overbought).
    rsi_bull_min: float = 50.0
    rsi_bull_max: float = 70.0
    # Only take shorts while RSI sits in this band (trend-confirming, not oversold).
    rsi_bear_min: float = 30.0
    rsi_bear_max: float = 50.0
    atr_period: int = 14
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 2.5
    # Restrict trading to the London/New York overlap (UTC hours), where XAUUSD
    # is most liquid and spreads are tightest — outside this window spreads on
    # gold widen enough to erode a 1-min scalp's edge.
    use_session_filter: bool = True
    session_start_hour: int = 7
    session_end_hour: int = 16

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskConfig:
    """Position sizing and cost-model parameters."""

    initial_balance: float = 10_000.0
    risk_per_trade_pct: float = 0.5  # % of current balance risked per trade
    # XAUUSD: 1 standard lot = 100 troy ounces. Price is quoted in USD/oz, so a
    # $1.00 move on 1 lot = $100 P&L.
    contract_size: float = 100.0
    min_lot: float = 0.01
    max_lot: float = 5.0
    lot_step: float = 0.01
    spread: float = 0.20  # USD/oz, applied against the trader at entry
    commission_per_lot: float = 7.0  # USD, round-turn, per standard lot
    max_open_positions: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str) -> tuple[StrategyConfig, RiskConfig]:
    """Load overrides from a JSON file; unspecified fields keep their defaults."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    strategy = StrategyConfig(**data.get("strategy", {}))
    risk = RiskConfig(**data.get("risk", {}))
    return strategy, risk


def save_config(path: str, strategy: StrategyConfig, risk: RiskConfig) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"strategy": strategy.to_dict(), "risk": risk.to_dict()}, fh, indent=2)
