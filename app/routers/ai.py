"""TuniAI — Router chat (Phase 1).

- POST /api/ai/chat  : chat với DeepSeek (stream hoặc không stream)
- GET  /api/ai/status: trạng thái cấu hình + tính năng (public)
Yêu cầu đăng nhập + rate limit + kiểm tra tính năng ai_chat (theo gói).
"""
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import Feature, PlanTierEnum, User
from app.services import ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = False


# Rate limit theo user (in-memory, đơn giản — đủ cho MVP)
_usage: dict[str, list[float]] = {}

_TIER_ORDER = {
    PlanTierEnum.free: 0,
    PlanTierEnum.basic: 1,
    PlanTierEnum.premium: 2,
    PlanTierEnum.pro: 3,
}


def _check_ai_access(db: Session, user: User) -> None:
    """Kiểm tra tính năng ai_chat trong bảng features (Phase 1 mặc định cho mọi user)."""
    feature = db.query(Feature).filter(Feature.key == "ai_chat").first()
    if feature is None:
        return  # chưa khai báo feature → mặc định cho dùng
    if not feature.enabled:
        raise HTTPException(status_code=403, detail="Tính năng TuniAI hiện đang tạm khóa.")
    required = _TIER_ORDER.get(feature.access_level, 0)
    user_tier = _TIER_ORDER.get(user.plan, 0)
    if user_tier < required:
        raise HTTPException(status_code=403, detail="Gói của bạn chưa bao gồm TuniAI. Vui lòng nâng cấp.")


def _check_rate_limit(user_id: int) -> None:
    window = settings.ai_rate_limit_minutes * 60
    now = time.time()
    key = str(user_id)
    times = [t for t in _usage.get(key, []) if now - t < window]
    if len(times) >= settings.ai_rate_limit_max:
        raise HTTPException(
            status_code=429,
            detail="Bạn đã hỏi TuniAI quá nhiều. Vui lòng thử lại sau vài phút.",
        )
    times.append(now)
    _usage[key] = times


@router.get("/status")
def ai_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    feature = db.query(Feature).filter(Feature.key == "ai_chat").first()
    return {
        "configured": bool(settings.deepseek_api_key),
        "model": settings.deepseek_model,
        "enabled": feature.enabled if feature else True,
        "access_level": feature.access_level if feature else "free",
    }


@router.get("/market-insight")
async def market_insight(force: bool = False) -> dict[str, Any]:
    """Nhận định thị trường 1 câu (dòng chữ chạy dưới header) — public, không cần đăng nhập."""
    return await ai_service.market_insight(force=force)


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_ai_access(db, user)
    _check_rate_limit(user.id)
    if not payload.messages:
        raise HTTPException(status_code=400, detail="Danh sách tin nhắn trống.")

    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]

    if payload.stream:
        async def gen():
            try:
                async for ev in ai_service.stream_chat(msgs):
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            except ai_service.AiConfigError as exc:
                yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        return await ai_service.chat(msgs)
    except ai_service.AiConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai_service.DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
