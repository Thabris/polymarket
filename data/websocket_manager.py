"""WebSocket manager with auto-reconnect for Polymarket real-time data."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable, Optional, Set

import websockets
from websockets.client import WebSocketClientProtocol

from config.settings import settings
from core.models import PriceUpdate

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connection to Polymarket for real-time data.

    Features:
    - Automatic reconnection with exponential backoff
    - Subscription management for multiple markets
    - Heartbeat/ping handling
    - Message callback system
    """

    def __init__(
        self,
        url: Optional[str] = None,
        on_message: Optional[Callable[[dict], None]] = None,
        on_price_update: Optional[Callable[[PriceUpdate], None]] = None,
    ):
        self.url = url or settings.polymarket_ws_url
        self.on_message = on_message
        self.on_price_update = on_price_update

        self._ws: Optional[WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_delay = settings.ws_reconnect_delay
        self._max_reconnect_delay = settings.ws_max_reconnect_delay
        self._subscribed_markets: Set[str] = set()
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        if self._ws is None:
            return False
        # Handle different websockets library versions
        # Newer versions (>=11.0) use state property instead of open attribute
        if hasattr(self._ws, "open"):
            return self._ws.open
        # Try state-based check for newer versions
        if hasattr(self._ws, "state"):
            try:
                from websockets.protocol import State
                return self._ws.state == State.OPEN
            except ImportError:
                pass
        # Fallback: assume connected if we have a websocket object
        return True

    async def connect(self) -> None:
        """Start the WebSocket connection."""
        if self._running:
            return

        self._running = True
        self._tasks.append(asyncio.create_task(self._connection_loop()))
        self._tasks.append(asyncio.create_task(self._message_processor()))
        logger.info("WebSocket manager started")

    async def disconnect(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()

        # Close WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.info("WebSocket manager stopped")

    async def subscribe(self, market_id: str) -> None:
        """
        Subscribe to updates for a market.

        Args:
            market_id: Market/token ID to subscribe to
        """
        self._subscribed_markets.add(market_id)

        if self.is_connected:
            await self._send_subscribe(market_id)

    async def unsubscribe(self, market_id: str) -> None:
        """
        Unsubscribe from a market.

        Args:
            market_id: Market/token ID to unsubscribe from
        """
        self._subscribed_markets.discard(market_id)

        if self.is_connected:
            await self._send_unsubscribe(market_id)

    async def _connection_loop(self) -> None:
        """Main connection loop with auto-reconnect."""
        delay = self._reconnect_delay

        while self._running:
            try:
                logger.info(f"Connecting to WebSocket: {self.url}")

                async with websockets.connect(
                    self.url,
                    ping_interval=settings.ws_ping_interval,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    delay = self._reconnect_delay  # Reset delay on success
                    logger.info("WebSocket connected")

                    # Resubscribe to all markets
                    for market_id in self._subscribed_markets:
                        await self._send_subscribe(market_id)

                    # Listen for messages
                    await self._receive_loop(ws)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            self._ws = None

            if self._running:
                logger.info(f"Reconnecting in {delay:.1f} seconds...")
                await asyncio.sleep(delay)
                # Exponential backoff
                delay = min(delay * 2, self._max_reconnect_delay)

    async def _receive_loop(self, ws: WebSocketClientProtocol) -> None:
        """Receive and queue messages from WebSocket."""
        async for message in ws:
            # Skip empty or binary messages
            if not message or not isinstance(message, str):
                continue
            # Skip ping/pong frames that might leak through
            if message.strip() in ("", "ping", "pong"):
                continue
            try:
                data = json.loads(message)
                await self._message_queue.put(data)
            except json.JSONDecodeError:
                # Only log if it looks like it should be JSON
                if message.startswith("{") or message.startswith("["):
                    logger.debug(f"Failed to parse WebSocket message: {message[:100]}")

    async def _message_processor(self) -> None:
        """Process messages from the queue."""
        while self._running:
            try:
                data = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0,
                )

                # Call generic message handler
                if self.on_message:
                    try:
                        self.on_message(data)
                    except Exception as e:
                        logger.error(f"Error in message handler: {e}")

                # Parse and call price update handler
                if self.on_price_update:
                    price_update = self._parse_price_update(data)
                    if price_update:
                        try:
                            self.on_price_update(price_update)
                        except Exception as e:
                            logger.error(f"Error in price update handler: {e}")

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    def _parse_price_update(self, data: dict) -> Optional[PriceUpdate]:
        """Parse a WebSocket message into a PriceUpdate."""
        try:
            # Polymarket WebSocket message format varies
            # This handles common formats

            if "price" in data:
                return PriceUpdate(
                    market_id=data.get("market", data.get("condition_id", "")),
                    token_id=data.get("asset_id", data.get("token_id", "")),
                    price=float(data["price"]),
                    timestamp=datetime.utcnow(),
                )

            # Order book update format
            if "bids" in data or "asks" in data:
                # Calculate midpoint from order book
                best_bid = None
                best_ask = None

                bids = data.get("bids", [])
                asks = data.get("asks", [])

                if bids:
                    best_bid = float(bids[0].get("price", 0))
                if asks:
                    best_ask = float(asks[0].get("price", 0))

                if best_bid and best_ask:
                    midpoint = (best_bid + best_ask) / 2
                    return PriceUpdate(
                        market_id=data.get("market", ""),
                        token_id=data.get("asset_id", ""),
                        price=midpoint,
                        timestamp=datetime.utcnow(),
                    )

            return None

        except Exception as e:
            logger.debug(f"Could not parse price update: {e}")
            return None

    async def _send_subscribe(self, market_id: str) -> None:
        """Send subscription message for a market."""
        if not self._ws:
            return

        try:
            message = {
                "type": "subscribe",
                "channel": "market",
                "market": market_id,
            }
            await self._ws.send(json.dumps(message))
            logger.debug(f"Subscribed to market: {market_id}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {market_id}: {e}")

    async def _send_unsubscribe(self, market_id: str) -> None:
        """Send unsubscription message for a market."""
        if not self._ws:
            return

        try:
            message = {
                "type": "unsubscribe",
                "channel": "market",
                "market": market_id,
            }
            await self._ws.send(json.dumps(message))
            logger.debug(f"Unsubscribed from market: {market_id}")
        except Exception as e:
            logger.error(f"Failed to unsubscribe from {market_id}: {e}")


# Global WebSocket manager instance
ws_manager = WebSocketManager()
