"""VinaF AI — Router chat (Phase 1).

- POST /api/ai/chat  : chat với DeepSeek (stream hoặc không stream)
- GET  /api/ai/status: trạng thái cấu hình + tính năng (public)
Yêu cầu đăng nhập + rate limit + kiểm tra tính năng ai_chat (theo gói).
"""
import json
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import (
    AiConversation,
    AiMemory,
    Feature,
    MemberQuota,
    Notification,
    NotificationType,
    PlanTierEnum,
    User,
)
from app.services import access, ai_service, feature_flags

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = False


class ConversationSave(BaseModel):
    """Lưu/ghi đè một hội thoại (upsert theo user + cid)."""

    title: str = ""
    messages: list[ChatMessage] = []


# Rate limit theo user (in-memory, đơn giản — đủ cho MVP)
_usage: dict[str, list[float]] = {}

_TIER_ORDER = {
    PlanTierEnum.free: 0,
    PlanTierEnum.basic: 1,
    PlanTierEnum.premium: 2,
    PlanTierEnum.pro: 3,
}


def _check_ai_access(db: Session, user: User) -> None:
    """Quyền dùng VinaF AI (chat + copilot) theo block 'ai_unlimited'.

    - Block MỞ (enabled=True) → mọi user đã đăng nhập đều dùng được.
    - Block KHOÁ (enabled=False) → chỉ hội viên đã mở gói dùng được.
    """
    if feature_flags.is_open(db, "ai_unlimited"):
        return
    if not access.is_active_member(user):
        raise HTTPException(status_code=403, detail="VinaF AI đang khoá hội viên. Vui lòng mở gói hội viên để sử dụng.")


def _check_rate_limit(user_id: int) -> None:
    window = settings.ai_rate_limit_minutes * 60
    now = time.time()
    key = str(user_id)
    times = [t for t in _usage.get(key, []) if now - t < window]
    if len(times) >= settings.ai_rate_limit_max:
        raise HTTPException(
            status_code=429,
            detail="Bạn đã hỏi VinaF AI quá nhiều. Vui lòng thử lại sau vài phút.",
        )
    times.append(now)
    _usage[key] = times


def _get_quota(db: Session, user: User) -> MemberQuota:
    period = access.period_key()
    row = (
        db.query(MemberQuota)
        .filter(MemberQuota.user_id == user.id, MemberQuota.period == period)
        .first()
    )
    if row is None:
        row = MemberQuota(user_id=user.id, period=period, ai_questions=0, ai_new_chats=0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _enforce_free_ai_question(db: Session, user: User) -> None:
    """Quyền hỏi VinaF AI theo block 'ai_unlimited'.

    - Block MỞ (enabled=True): mọi user (kể cả chưa mua gói) dùng KHÔNG GIỚI HẠN.
    - Block KHOÁ (enabled=False): chỉ hội viên đã mở gói được dùng.
    """
    if feature_flags.is_open(db, "ai_unlimited"):
        return
    if not access.is_active_member(user):
        raise HTTPException(
            status_code=403,
            detail="VinaF AI đang khoá hội viên. Vui lòng mở gói hội viên để sử dụng.",
        )


def _get_or_create_memory(db: Session, user_id: int) -> AiMemory:
    row = db.query(AiMemory).filter(AiMemory.user_id == user_id).first()
    if row is None:
        row = AiMemory(user_id=user_id, memory="", recent="[]")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _recent_turns(row: AiMemory) -> list:
    try:
        val = json.loads(row.recent or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


async def _update_memory(db: Session, row: AiMemory, turn_msgs: list[dict[str, Any]]) -> None:
    """Lưu các lượt gần đây + tổng hợp bộ nhớ người dùng (best-effort, không chặn chat)."""
    recent = _recent_turns(row) + turn_msgs
    recent = recent[-40:]
    row.recent = json.dumps(recent, ensure_ascii=False, default=str)
    try:
        row.memory = await ai_service.summarize_memory(row.memory or "", recent[-20:])
    except Exception:  # noqa: BLE001 — lỗi memory không được làm hỏng câu trả lời
        pass
    db.commit()


@router.get("/status")
def ai_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    feature = db.query(Feature).filter(Feature.key == "ai_chat").first()
    return {
        "configured": bool(settings.deepseek_api_key),
        "model": settings.deepseek_model,
        "enabled": feature.enabled if feature else True,
        "access_level": feature.access_level if feature else "free",
    }


@router.get("/copilot")
def ai_copilot(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI Investment Copilot — quét danh mục, trả về thay đổi đáng chú ý.
    Đồng thời tạo thông báo (chuông) idempotent theo ref_key copilot:...:ngày.
    """
    _check_ai_access(db, user)
    from app.services.copilot_service import CopilotService

    try:
        result = CopilotService.scan(user.id)
    except Exception as exc:  # noqa: BLE001
        return {"empty": False, "summary": "Tạm thời chưa quét được danh mục.", "changes": [], "error": str(exc)}

    # Tạo thông báo (bell) cho từng thay đổi — dedupe theo (user, type, ref_key).
    try:
        today = date.today().isoformat()
        for c in result.get("changes") or []:
            ref = f"copilot:{c.get('symbol')}:{c.get('type')}:{today}"
            exists = (
                db.query(Notification)
                .filter(
                    Notification.user_id == user.id,
                    Notification.type == NotificationType.stock,
                    Notification.ref_key == ref,
                )
                .first()
            )
            if exists:
                continue
            db.add(
                Notification(
                    user_id=user.id,
                    type=NotificationType.stock,
                    title=c.get("title", ""),
                    body=c.get("reason", ""),
                    symbol=c.get("symbol"),
                    link="/quan-ly-tai-san",
                    ref_key=ref,
                )
            )
        db.commit()
    except Exception:  # noqa: BLE001 — thông báo lỗi không làm hỏng kết quả
        pass
    return result


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_ai_access(db, user)
    _enforce_free_ai_question(db, user)
    _check_rate_limit(user.id)
    if not payload.messages:
        raise HTTPException(status_code=400, detail="Danh sách tin nhắn trống.")

    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    mem_row = _get_or_create_memory(db, user.id)
    memory = (mem_row.memory or "").strip()

    if payload.stream:
        async def gen():
            full_reply = ""
            try:
                async for ev in ai_service.stream_chat(msgs, memory=memory, user_id=user.id):
                    if ev.get("type") == "delta":
                        full_reply += str(ev.get("content") or "")
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            except ai_service.AiConfigError as exc:
                yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
            except Exception:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'content': 'Lỗi kết nối tới VinaF AI.'}, ensure_ascii=False)}\n\n"
            # Cập nhật bộ nhớ (best-effort) TRƯỚC khi báo DONE để đảm bảo chạy
            try:
                turns = []
                last = msgs[-1] if msgs else {}
                if last.get("content"):
                    turns.append({"role": "user", "content": str(last["content"])})
                if full_reply:
                    turns.append({"role": "assistant", "content": full_reply})
                if turns:
                    await _update_memory(db, mem_row, turns)
            except Exception:  # noqa: BLE001
                pass
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        result = await ai_service.chat(msgs, memory=memory, user_id=user.id)
    except ai_service.AiConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai_service.DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Cập nhật bộ nhớ (best-effort) sau khi trả lời xong
    try:
        reply = (result.get("reply") or "").strip()
        turns = []
        last = msgs[-1] if msgs else {}
        if last.get("content"):
            turns.append({"role": "user", "content": str(last["content"])})
        if reply:
            turns.append({"role": "assistant", "content": reply})
        if turns:
            await _update_memory(db, mem_row, turns)
    except Exception:  # noqa: BLE001
        pass
    return result


# ---------- Lịch sử hội thoại (theo tài khoản) ----------

def _conv_messages(row: AiConversation) -> list[dict[str, Any]]:
    try:
        val = json.loads(row.messages or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _conv_out(row: AiConversation) -> dict[str, Any]:
    return {
        "id": row.cid,
        "title": row.title or "",
        "messages": _conv_messages(row),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id)
        .order_by(AiConversation.updated_at.desc())
        .limit(50)
        .all()
    )
    return [_conv_out(r) for r in rows]


@router.get("/conversations/{cid}")
def get_conversation(
    cid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.cid == cid)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại.")
    return _conv_out(row)


@router.put("/conversations/{cid}")
def save_conversation(
    cid: str,
    payload: ConversationSave,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.cid == cid)
        .first()
    )
    if row is None:
        # Tạo trò chuyện MỚI: block 'ai_unlimited' MỞ → không giới hạn; KHOÁ → chỉ hội viên.
        if not feature_flags.is_open(db, "ai_unlimited") and not access.is_active_member(user):
            raise HTTPException(
                status_code=403,
                detail="VinaF AI đang khoá hội viên. Vui lòng mở gói hội viên để sử dụng.",
            )
        row = AiConversation(user_id=user.id, cid=cid)
        db.add(row)
    row.title = (payload.title or "")[:255]
    row.messages = json.dumps(
        [{"role": m.role, "content": m.content} for m in payload.messages],
        ensure_ascii=False,
        default=str,
    )
    db.commit()
    return {"ok": True}


@router.delete("/conversations/{cid}")
def delete_conversation(
    cid: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.cid == cid)
        .first()
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}
