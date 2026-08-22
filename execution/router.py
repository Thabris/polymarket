"""Execution seam: the router interface every fill path goes through.

Scanners never place orders. They publish Signals; the SignalService hands
each accepted signal to the configured ExecutionRouter. v1 ships only the
PaperRouter; a future LiveRouter (py-clob-client) implements the same
interface and receives the same RiskLimits.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from config.settings import settings
from signals.models import Signal


class RiskLimits(BaseModel):
    """Risk configuration enforced by routers (paper now, live later)."""

    max_position_per_market: float = settings.risk_max_position_per_market
    max_open_positions_per_strategy: int = settings.risk_max_open_per_strategy
    max_daily_loss: float = settings.risk_max_daily_loss


class ExecutionRouter(ABC):
    """Interface between accepted signals and (paper or live) fills."""

    def __init__(self, risk_limits: RiskLimits | None = None) -> None:
        self.risk = risk_limits or RiskLimits()

    @abstractmethod
    async def on_signal(self, signal: Signal) -> None:
        """Handle a newly accepted signal (open a position or a pending order)."""

    @abstractmethod
    async def close_position(self, position_id: int, reason: str, price: float) -> None:
        """Close a position at a given price."""

    @abstractmethod
    def status(self) -> dict:
        """Router state for the status API."""
