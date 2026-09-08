from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db import get_db
from app.models import User
from app.services import crypto_signal_service as css

router = APIRouter(prefix="/crypto-signals", tags=["crypto-signals"])


@router.get("")
def list_crypto_signals(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return css.latest(db, user)


@router.post("/scan")
async def scan_crypto_signals(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return await css.scan_user(db, user)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — trả lỗi rõ ràng để UI hiển thị
        raise HTTPException(status_code=503, detail=str(exc) or "Quét tín hiệu Crypto thất bại.")
