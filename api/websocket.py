"""WebSocket endpoint for real-time data streaming."""

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.events import Event, EventType, event_bus
from core.models import Alert

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections and broadcasting."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._subscribed = False

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total: {len(self.active_connections)}")

        # Subscribe to events on first connection
        if not self._subscribed:
            await self._subscribe_to_events()

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict) -> None:
        """Send a message to all connected clients."""
        if not self.active_connections:
            return

        message_json = json.dumps(message, default=str)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception:
                disconnected.add(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.active_connections.discard(conn)

    async def _subscribe_to_events(self) -> None:
        """Subscribe to event bus events for broadcasting."""
        event_bus.subscribe(EventType.ALERT_CREATED, self._handle_alert)
        event_bus.subscribe(EventType.PRICE_UPDATE, self._handle_price_update)
        event_bus.subscribe(EventType.TRADE, self._handle_trade)
        self._subscribed = True
        logger.info("WebSocket manager subscribed to events")

    async def _handle_alert(self, event: Event) -> None:
        """Handle alert event and broadcast to clients."""
        if isinstance(event.data, Alert):
            await self.broadcast({
                "type": "alert",
                "data": {
                    "id": event.data.id,
                    "alert_type": event.data.alert_type.value,
                    "severity": event.data.severity.value,
                    "title": event.data.title,
                    "message": event.data.message,
                    "market_id": event.data.market_id,
                    "created_at": event.data.created_at.isoformat(),
                },
            })

    async def _handle_price_update(self, event: Event) -> None:
        """Handle price update event and broadcast to clients."""
        data = event.data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        elif hasattr(data, "__dict__"):
            data = data.__dict__

        await self.broadcast({
            "type": "price_update",
            "data": data,
        })

    async def _handle_trade(self, event: Event) -> None:
        """Handle trade event and broadcast to clients."""
        data = event.data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        elif hasattr(data, "__dict__"):
            data = data.__dict__

        await self.broadcast({
            "type": "trade",
            "data": data,
        })


# Global connection manager
manager = ConnectionManager()


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time data streaming.

    Streams:
    - Alert notifications
    - Price updates
    - Trade events

    Message format:
    {
        "type": "alert" | "price_update" | "trade",
        "data": {...}
    }
    """
    await manager.connect(websocket)

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "data": {"message": "Connected to Polymarket Monitor"},
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for messages (ping/pong or commands)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )

                # Handle commands from client
                try:
                    message = json.loads(data)
                    await _handle_client_message(websocket, message)
                except json.JSONDecodeError:
                    pass

            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)


async def _handle_client_message(websocket: WebSocket, message: dict) -> None:
    """Handle messages from WebSocket clients."""
    msg_type = message.get("type")

    if msg_type == "pong":
        # Client responded to ping
        pass

    elif msg_type == "subscribe":
        # Client wants to subscribe to specific market
        market_id = message.get("market_id")
        if market_id:
            await websocket.send_json({
                "type": "subscribed",
                "data": {"market_id": market_id},
            })

    elif msg_type == "unsubscribe":
        # Client wants to unsubscribe from specific market
        market_id = message.get("market_id")
        if market_id:
            await websocket.send_json({
                "type": "unsubscribed",
                "data": {"market_id": market_id},
            })
