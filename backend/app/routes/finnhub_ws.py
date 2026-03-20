import asyncio
import json
import os

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
US_SYMBOLS  = ["AMZN", "NVDA"]

connected_clients: list[WebSocket] = []
latest_prices: dict[str, float]    = {}


async def finnhub_listener():
    while True:
        if not FINNHUB_KEY:
            await asyncio.sleep(60)
            continue

        uri = f"wss://ws.finnhub.io?token={FINNHUB_KEY}"
        try:
            async with websockets.connect(uri) as ws:
                for sym in US_SYMBOLS:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": sym}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "trade" and msg.get("data"):
                        for trade in msg["data"]:
                            sym   = trade.get("s")
                            price = trade.get("p")
                            if sym and price is not None:
                                latest_prices[sym] = round(float(price), 2)
                                payload = json.dumps({"sym": sym, "price": latest_prices[sym]})
                                dead = []
                                for client in connected_clients:
                                    try:
                                        await client.send_text(payload)
                                    except Exception:
                                        dead.append(client)
                                for d in dead:
                                    if d in connected_clients:
                                        connected_clients.remove(d)
        except Exception:
            await asyncio.sleep(5)


@router.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        for sym, price in latest_prices.items():
            await websocket.send_text(json.dumps({"sym": sym, "price": price}))
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
