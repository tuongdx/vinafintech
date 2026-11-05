"""API PUBLIC cho VinaFintech Signal Engine (spec §19-§20).

CHỈ trả dữ liệu PHÂN TÍCH: trend / momentum / RSI / MA / volume / CMF / relative strength /
AI score / risk score / signal score / setup strength / market state / điều kiện hội tụ.

TUYỆT ĐỐI KHÔNG trả BUY/SELL/Entry/Stop Loss/Take Profit/Trailing Stop (spec §4, §33.2-33.4)
— thực hiện ở tầng serializer bằng whitelist (``serializers.public_view`` + ``assert_public_clean``),
không phải bằng cách ẩn ở frontend.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.signal_engine import engine as engine_module
from app.services.signal_engine import serializers

router = APIRouter(prefix="/stocks", tags=["signal-public"])


def _snapshot_or_404(symbol: str) -> dict[str, Any]:
    cleaned = _clean_symbol(symbol)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Mã không hợp lệ")
    snapshot = engine_module.analyze_symbol(cleaned)
    if snapshot.get("status") == "DATA_UNAVAILABLE":
        raise HTTPException(status_code=404, detail=f"Không có dữ liệu cho mã {cleaned}")
    return snapshot


@router.get("/{symbol}/analysis")
def analysis(symbol: str) -> dict[str, Any]:
    """Phân tích đầy đủ dành cho USER (không có khuyến nghị mua/bán)."""
    return serializers.public_view(_snapshot_or_404(symbol))


@router.get("/{symbol}/scores")
def scores(symbol: str) -> dict[str, Any]:
    """Chỉ phần điểm (VinaFintech Signal Score + Risk + AI)."""
    return serializers.public_scores(_snapshot_or_404(symbol))


@router.get("/{symbol}/setup")
def setup(symbol: str) -> dict[str, Any]:
    """Trạng thái setup + các điều kiện đang hội tụ."""
    return serializers.public_setup(_snapshot_or_404(symbol))


def _clean_symbol(value: str) -> str:
    text = str(value or "").strip().upper()
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in (".", "-"))
    return cleaned[:12]
