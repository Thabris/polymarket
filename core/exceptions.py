"""Custom exceptions for Polymarket Monitor."""


class PolymarketError(Exception):
    """Base exception for Polymarket Monitor."""

    pass


class ConnectionError(PolymarketError):
    """Connection-related errors."""

    pass


class AuthenticationError(PolymarketError):
    """Authentication/credential errors."""

    pass


class RateLimitError(PolymarketError):
    """Rate limit exceeded error."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = retry_after


class MarketNotFoundError(PolymarketError):
    """Market not found error."""

    def __init__(self, market_id: str):
        super().__init__(f"Market not found: {market_id}")
        self.market_id = market_id


class InvalidOrderError(PolymarketError):
    """Invalid order parameters."""

    pass


class InsufficientBalanceError(PolymarketError):
    """Insufficient balance for trade."""

    pass


class WebSocketError(ConnectionError):
    """WebSocket-specific errors."""

    pass


class ConfigurationError(PolymarketError):
    """Configuration/settings errors."""

    pass
