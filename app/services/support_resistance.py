"""Dịch vụ phát hiện vùng Hỗ trợ / Kháng cự (Support & Resistance).

KHÔNG hard-code 1 giá. Mỗi vùng là một dải giá với nhiều thuộc tính định lượng:
    {symbol, kind, lower_bound, upper_bound, pivot_price, strength_score,
     confidence, touch_count, volume_confirmation, timeframe}

Thuật toán:
    1. Tìm swing high / swing low (pivot fractal, window = 2).
    2. Gom cụm (cluster) các pivot gần nhau theo tolerance % → mỗi cụm là 1 vùng.
    3. Đếm số lần chạm (touch_count) bằng candle high/low nằm trong dải vùng.
    4. Xác nhận khối lượng (volume_confirmation) = khối lượng các phiên chạm so trung bình.
    5. Tính strength_score (0-100) + confidence (HIGH/MEDIUM/LOW).
"""
from __future__ import annotations

from typing import Any

PIVOT_WINDOW = 2          # số phiên mỗi bên để xác nhận swing high/low
CLUSTER_TOLERANCE_PCT = 0.004   # 0.4% — sai số gom cụm
MIN_TOUCHES = 2           # tối thiểu số lần chạm để coi là vùng đáng tin
VOLUME_CONFIRM_RATIO = 1.2


class SupportResistanceService:
    """Thuần hàm (stateless) — dễ test, dễ mở rộng."""

    @staticmethod
    def _float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _find_pivots(cls, candles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Trả về (swing_highs, swing_lows): list {price, index, volume}."""
        highs: list[dict[str, Any]] = []
        lows: list[dict[str, Any]] = []
        n = len(candles)
        w = PIVOT_WINDOW
        for i in range(w, n - w):
            hi = cls._float(candles[i].get("high"))
            lo = cls._float(candles[i].get("low"))
            vol = cls._float(candles[i].get("volume"))
            is_high = all(hi >= cls._float(candles[j].get("high")) for j in range(i - w, i + w + 1))
            is_low = all(lo <= cls._float(candles[j].get("low")) for j in range(i - w, i + w + 1))
            if is_high:
                highs.append({"price": hi, "index": i, "volume": vol})
            if is_low:
                lows.append({"price": lo, "index": i, "volume": vol})
        return highs, lows

    @classmethod
    def _cluster(cls, pivots: list[dict[str, Any]], tol_pct: float) -> list[list[dict[str, Any]]]:
        """Gom các pivot gần nhau thành cụm (theo tolerance %)."""
        if not pivots:
            return []
        pivots = sorted(pivots, key=lambda p: p["price"])
        clusters: list[list[dict[str, Any]]] = []
        current = [pivots[0]]
        for p in pivots[1:]:
            ref = current[0]["price"]
            if abs(p["price"] - ref) / ref <= tol_pct:
                current.append(p)
            else:
                clusters.append(current)
                current = [p]
        clusters.append(current)
        return clusters

    @classmethod
    def _zone_from_cluster(cls, symbol: str, kind: str, cluster: list[dict[str, Any]],
                           candles: list[dict[str, Any]], timeframe: str) -> dict[str, Any] | None:
        prices = [p["price"] for p in cluster]
        center = sum(prices) / len(prices)
        half = max(abs(p - center) for p in prices) if len(prices) > 1 else center * CLUSTER_TOLERANCE_PCT
        pad = max(half, center * CLUSTER_TOLERANCE_PCT)
        lower_bound = center - pad
        upper_bound = center + pad

        # Đếm số lần chạm: candle high (resistance) / low (support) rơi vào dải vùng.
        touches: list[float] = []
        touch_volumes: list[float] = []
        all_volumes: list[float] = []
        for c in candles:
            vol = cls._float(c.get("volume"))
            all_volumes.append(vol)
            if kind == "resistance":
                hi = cls._float(c.get("high"))
                if hi >= lower_bound and hi <= upper_bound:
                    touches.append(hi)
                    touch_volumes.append(vol)
            else:
                lo = cls._float(c.get("low"))
                if lo >= lower_bound and lo <= upper_bound:
                    touches.append(lo)
                    touch_volumes.append(vol)

        touch_count = len(set(round(t, 2) for t in touches))  # đếm số lần chạm khác nhau
        avg_all = (sum(all_volumes) / len(all_volumes)) if all_volumes else 0.0
        avg_touch = (sum(touch_volumes) / len(touch_volumes)) if touch_volumes else 0.0
        volume_confirmation = bool(avg_all > 0 and avg_touch / avg_all >= VOLUME_CONFIRM_RATIO)

        if touch_count < MIN_TOUCHES:
            return None

        # strength_score (0-100): trọng số touch + volume + độ khít của cụm + độ gần đáy.
        touch_score = min(touch_count / 5.0, 1.0) * 40
        vol_score = 20 if volume_confirmation else 5
        spread_score = max(0.0, 1.0 - (half / center) / (CLUSTER_TOLERANCE_PCT * 3)) * 20
        depth_score = min(len(cluster) / 4.0, 1.0) * 20
        strength = int(round(touch_score + vol_score + spread_score + depth_score))
        strength = max(0, min(100, strength))

        if strength >= 70:
            confidence = "HIGH"
        elif strength >= 45:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return {
            "symbol": symbol,
            "kind": kind,
            "lower_bound": round(lower_bound, 2),
            "upper_bound": round(upper_bound, 2),
            "pivot_price": round(center, 2),
            "strength_score": strength,
            "confidence": confidence,
            "touch_count": touch_count,
            "volume_confirmation": volume_confirmation,
            "timeframe": timeframe,
        }

    @classmethod
    def detect_zones(cls, symbol: str, candles: list[dict[str, Any]],
                     current_price: float | None = None, timeframe: str = "1D") -> list[dict[str, Any]]:
        """Trả về danh sách vùng Hỗ trợ (dưới giá) và Kháng cự (trên giá)."""
        if not candles or len(candles) < PIVOT_WINDOW * 2 + 3:
            return []

        highs, lows = cls._find_pivots(candles)
        zones: list[dict[str, Any]] = []
        for cluster in cls._cluster(highs, CLUSTER_TOLERANCE_PCT):
            z = cls._zone_from_cluster(symbol, "resistance", cluster, candles, timeframe)
            if z:
                zones.append(z)
        for cluster in cls._cluster(lows, CLUSTER_TOLERANCE_PCT):
            z = cls._zone_from_cluster(symbol, "support", cluster, candles, timeframe)
            if z:
                zones.append(z)

        price = cls._float(current_price) or cls._float(candles[-1].get("close"))
        if price > 0:
            zones = [z for z in zones
                     if (z["kind"] == "support" and z["upper_bound"] < price)
                     or (z["kind"] == "resistance" and z["lower_bound"] > price)]
            for z in zones:
                z["distance_percent"] = round(abs(z["pivot_price"] - price) / price * 100.0, 2)

        # Sắp xếp: vùng gần giá nhất trước.
        zones.sort(key=lambda z: z.get("distance_percent", 999))
        return zones

    @classmethod
    def nearest_zones(cls, symbol: str, candles: list[dict[str, Any]],
                      current_price: float | None = None, timeframe: str = "1D",
                      limit_per_side: int = 2) -> dict[str, Any]:
        """Vùng Hỗ trợ + Kháng cự GẦN NHẤT (dùng cho bảng danh mục / metadata alert)."""
        zones = cls.detect_zones(symbol, candles, current_price, timeframe)
        support = [z for z in zones if z["kind"] == "support"][:limit_per_side]
        resistance = [z for z in zones if z["kind"] == "resistance"][:limit_per_side]
        return {"support": support, "resistance": resistance}


# Singleton tiện dùng
sr_service = SupportResistanceService()
