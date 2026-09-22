"""MODWT — Maximal Overlap Discrete Wavelet Transform (thuần Python).

Vì sao MODWT (spec §6 - Model 1):
- Không decimate → giữ NGUYÊN độ dài chuỗi ⇒ KHÔNG lệch timing như DWT cổ điển.
- Phân tách thành nhiều tầng tần số: tầng thấp (smooth) = xu hướng dài hạn,
  tầng cao (detail 1-2) = biến động ngắn hạn/nhiễu.
- Bất biến với phép dịch (shift-invariant) → tín hiệu cùng hình dạng ở thời điểm
  khác nhau cho cùng kết quả.

Bộ lọc: Haar (L=2) với quy ước MODWT (÷√2 so với DWT) ⇒ khai triển đa phân giải
CỘNG ĐƯỢC CHÍNH XÁC: ``x = c_J + Σ d_j`` (kiểm chứng trong test).

LƯU Ý TRUNG THỰC: đây là **feature engineering tất định**, KHÔNG phải model đã train.
Nó chỉ cung cấp cấu trúc đa tần số cho các tầng sau (Transformer) và cho scoring.
``energy_share`` là tỉ trọng năng lượng TƯƠNG ĐỐI giữa các tầng trong cùng 1 chuỗi
(không phải phân rã phương sai trực giao).
"""
from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)
# Haar MODWT: h = [1/2, 1/2], g = [1/2, -1/2]  (DWT filters ÷ √2)
_H0, _H1 = 0.5, 0.5
_G0, _G1 = 0.5, -0.5


def modwt(values: list[float], levels: int = 4) -> dict:
    """Khai triển MODWT (à trous) — trả các tầng detail + smooth, cùng độ dài input."""
    n = len(values)
    if n == 0 or levels < 1:
        return {"levels": [], "smooth": list(values), "length": n}
    x = [float(v) for v in values]
    details: list[list[float]] = []
    current = x
    for j in range(1, levels + 1):
        shift = 1 << (j - 1)  # 2^(j-1): giãn bộ lọc theo tầng
        if shift >= n:
            break
        detail = [0.0] * n
        smooth = [0.0] * n
        for t in range(n):
            prev = current[(t - shift) % n]
            detail[t] = _G0 * current[t] + _G1 * prev      # hiệu (tần số cao)
            smooth[t] = _H0 * current[t] + _H1 * prev      # trung bình (tần số thấp)
        details.append(detail)
        current = smooth
    return {
        "levels": details,
        "smooth": current,
        "length": n,
        "scale_gain": 1.0,  # quy ước MODWT ⇒ tầng đã ở đúng biên độ
    }


def reconstruct(decomp: dict) -> list[float]:
    """Dựng lại chuỗi từ MRA (bằng chứng không mất mát thông tin)."""
    n = decomp.get("length") or len(decomp.get("smooth") or [])
    out = list(decomp.get("smooth") or [0.0] * n)
    for detail in decomp.get("levels") or []:
        for i in range(n):
            out[i] += detail[i]
    return out


def _variance(series: list[float]) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    mean = sum(series) / n
    return sum((v - mean) ** 2 for v in series) / n


def _slope_pct(series: list[float], lookback: int) -> float | None:
    if len(series) <= lookback:
        return None
    base = series[-1 - lookback]
    if not base:
        return None
    return (series[-1] / base - 1) * 100


def wavelet_features(closes: list[float], levels: int = 4, slope_lookback: int = 21) -> dict:
    """Feature đa tần số từ MODWT: tỉ trọng năng lượng theo tầng + xu hướng tầng thấp."""
    if len(closes) < max(30, levels * 4):
        return {"available": False, "reason": "INSUFFICIENT_DATA"}

    decomp = modwt(closes, levels=levels)
    details = decomp["levels"]
    smooth = decomp["smooth"]
    if not details:
        return {"available": False, "reason": "INSUFFICIENT_DATA"}

    energies = [_variance(d) for d in details]
    total = sum(energies)
    shares = [round(e / total, 4) if total > 0 else 0.0 for e in energies]

    high_freq = sum(shares[:2]) if len(shares) >= 2 else (shares[0] if shares else 0.0)
    low_freq = shares[-1] if shares else 0.0

    smooth_slope = _slope_pct(smooth, slope_lookback)
    smooth_fast = _slope_pct(smooth, 5)
    direction = "FLAT"
    if smooth_slope is not None:
        if smooth_slope > 1.0:
            direction = "UP"
        elif smooth_slope < -1.0:
            direction = "DOWN"

    return {
        "available": True,
        "levels": len(details),
        "energy_share": shares,                 # theo tầng 1..J (1 = nhiễu ngắn hạn nhất)
        "level_energy": [round(e, 6) for e in energies],
        "high_frequency_energy_share": round(high_freq, 4),
        "low_frequency_energy_share": round(low_freq, 4),
        "noise_ratio": round(high_freq, 4),     # năng lượng nhiễu / tổng năng lượng chi tiết
        "smooth_slope_pct": round(smooth_slope, 3) if smooth_slope is not None else None,
        "smooth_slope_short_pct": round(smooth_fast, 3) if smooth_fast is not None else None,
        "smooth_trend": direction,
        "last_smooth": round(smooth[-1], 4) if smooth else None,
    }
