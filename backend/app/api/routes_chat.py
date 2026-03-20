from __future__ import annotations

from typing import Literal, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class StockChatRequest(BaseModel):
    currentTicker: Optional[str] = None
    currentPrice: Optional[float] = None
    predictedPrice: Optional[float] = None
    messages: list[ChatMessage] = Field(default_factory=list)


class StockChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=StockChatResponse)
async def chat(payload: StockChatRequest) -> StockChatResponse:
    if not settings.ANTHROPIC_API_KEY:
        return StockChatResponse(
            reply="Anthropic API key is not configured on the server. Please set ANTHROPIC_API_KEY."
        )

    # Build a compact system prompt with any available ticker context.
    ticker_line = (
        f"Ticker: {payload.currentTicker}\n"
        f"Current price: {payload.currentPrice}\n"
        f"Predicted price (7d): {payload.predictedPrice}"
    ).strip()

    system_prompt = (
        "You are Pulse Stock Assistant, a helpful finance-focused chat assistant.\n"
        "Use the provided ticker context (if available) to explain what it means and answer user questions.\n"
        "Be clear, practical, and cautious. Do NOT claim guaranteed returns. Provide general educational info, not personal financial advice.\n"
        + ("\n\n" + ticker_line if payload.currentTicker else "\n\nNo ticker context provided.\n")
    )

    # Anthropic Messages API expects `messages` with `content` blocks.
    anthropic_messages = []
    for msg in payload.messages[-12:]:
        anthropic_messages.append(
            {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.text}],
            }
        )

    # If the client forgot to send history, at least respond to the last message.
    if not anthropic_messages:
        anthropic_messages = [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}]

    req_payload = {
        "model": settings.ANTHROPIC_MODEL,
        "system": system_prompt,
        "max_tokens": 500,
        "temperature": 0.5,
        "messages": anthropic_messages,
    }

    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=req_payload,
        )

    if res.status_code < 200 or res.status_code >= 300:
        # Include API error details to help debugging locally (kept short).
        detail = res.text[:300] if res.text else "Anthropic request failed."
        return StockChatResponse(reply=f"Unable to generate reply right now: {detail}")

    data = res.json()
    # Response shape: { content: [{ type: "text", text: "..." }], ... }
    content = data.get("content") or []
    text = ""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text") or ""
            break

    if not text:
        return StockChatResponse(reply="I received an empty reply from the model.")

    return StockChatResponse(reply=text)

