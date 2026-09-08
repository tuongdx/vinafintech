from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services import exchange_service
from app.services.exchange_service import CODE_TO_NAME, ExchangeRateError, SUPPORTED_CURRENCIES

router = APIRouter(prefix="/exchange", tags=["exchange"])

_SUPPORTED = {c["code"] for c in SUPPORTED_CURRENCIES}


class ConvertBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_code: str = Field(alias="from")
    to_code: str = Field(alias="to")
    amount: float = Field(gt=0)
    fee_percent: float = Field(default=0.0, ge=0.0, le=100.0)


@router.get("/rates")
def list_rates(refresh: bool = Query(False)):
    """Toàn bộ tỷ giá (quy VND) của các loại tiền tệ được hỗ trợ.

    - refresh=true → ép gọi provider (nếu lỗi vẫn trả cache cũ kèm cờ stale).
    """
    try:
        if refresh:
            return exchange_service.refresh_now()
        return exchange_service.load_payload()
    except ExchangeRateError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/rate")
def get_rate(base: str = Query("VND"), quote: str = Query("USD")):
    """Quy ước: rate = số đơn vị `base` cho 1 đơn vị `quote` (vd 1 USD = rate VND)."""
    base, quote = base.upper(), quote.upper()
    if base not in _SUPPORTED or quote not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="Loại tiền không được hỗ trợ.")
    try:
        return exchange_service.pair_rate(base, quote)
    except ExchangeRateError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/currencies")
def list_currencies():
    """Danh sách loại tiền được hỗ trợ (code + tên + số thập phân hiển thị)."""
    return {"count": len(SUPPORTED_CURRENCIES), "currencies": SUPPORTED_CURRENCIES}


@router.post("/convert")
def convert(body: ConvertBody):
    """Chuyển đổi từ -> to (fee % trừ trên số tiền nguồn, sau đó quy đổi)."""
    frm, to = body.from_code.upper(), body.to_code.upper()
    if frm not in _SUPPORTED or to not in _SUPPORTED:
        raise HTTPException(status_code=400, detail="Loại tiền không được hỗ trợ.")
    try:
        return exchange_service.convert(frm, to, body.amount, body.fee_percent)
    except ExchangeRateError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
