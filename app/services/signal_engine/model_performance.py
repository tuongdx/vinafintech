"""Model Performance (spec §25) — chất lượng từng model AI.

Dữ liệu lấy từ ``signal_predictions`` (chỉ có dòng khi model THỰC SỰ chạy) ghép với kết
quả signal đã đóng để tính accuracy/precision/recall/FN/FP.

Hiện project CHƯA train model ⇒ bảng rỗng ⇒ trả ``status = MODEL_NOT_TRAINED`` cùng các
chỉ số ``None`` (KHÔNG bịa số), kèm hướng dẫn cần gì để có dữ liệu.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Signal, SignalPrediction


def stats(db: Session) -> dict[str, Any]:
    rows = db.query(SignalPrediction).order_by(SignalPrediction.id.desc()).limit(5000).all()
    per_model: dict[str, dict[str, Any]] = {}

    if not rows:
        return {
            "status": "MODEL_NOT_TRAINED",
            "models": {},
            "note": "Chưa có model AI nào được train ⇒ không có prediction để đánh giá. "
                    "Chỉ số accuracy/precision/recall sẽ có khi model được train và ghi vào signal_predictions.",
            "predictions_recorded": 0,
        }

    # Kết quả thật của signal tại cùng mã + thời điểm (đã đóng, có return_pct)
    for pred in rows:
        bucket = per_model.setdefault(str(pred.model), {
            "predictions": 0, "evaluated": 0, "correct": 0,
            "true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0,
            "directions": {},
        })
        bucket["predictions"] += 1
        bucket["directions"][str(pred.direction or "UNKNOWN")] = bucket["directions"].get(str(pred.direction or "UNKNOWN"), 0) + 1

        signal = (
            db.query(Signal)
            .filter(Signal.symbol == pred.symbol, Signal.as_of == pred.as_of)
            .order_by(Signal.id.desc())
            .first()
        )
        if not signal or signal.return_pct is None:
            continue
        bucket["evaluated"] += 1
        actual_up = float(signal.return_pct) > 0
        predicted_up = str(pred.direction) == "BULLISH"
        if predicted_up and actual_up:
            bucket["true_positive"] += 1
            bucket["correct"] += 1
        elif predicted_up and not actual_up:
            bucket["false_positive"] += 1
        elif not predicted_up and actual_up:
            bucket["false_negative"] += 1
        else:
            bucket["true_negative"] += 1
            bucket["correct"] += 1

    out: dict[str, Any] = {}
    for model, bucket in per_model.items():
        evaluated = bucket["evaluated"]
        tp, fp, fn, tn = bucket["true_positive"], bucket["false_positive"], bucket["false_negative"], bucket["true_negative"]
        out[model] = {
            "predictions": bucket["predictions"],
            "evaluated": evaluated,
            "directions": bucket["directions"],
            "accuracy": round(bucket["correct"] / evaluated, 4) if evaluated else None,
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
            "false_positive": fp,
            "false_negative": fn,
            "prediction_stability": _stability(bucket["directions"], bucket["predictions"]),
        }

    return {
        "status": "OK" if any(v["evaluated"] for v in out.values()) else "NO_EVALUATED_PREDICTIONS",
        "models": out,
        "predictions_recorded": len(rows),
        "note": "Accuracy/precision/recall chỉ tính trên các prediction ĐÃ có kết quả signal thật (không nội suy).",
    }


def _stability(directions: dict[str, int], total: int) -> float | None:
    """Tỉ lệ prediction thuộc hướng chiếm ưu thế — càng cao càng ổn định (không đổi hướng liên tục)."""
    if not total:
        return None
    return round(max(directions.values()) / total, 4)
