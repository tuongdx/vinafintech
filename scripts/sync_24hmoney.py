"""Đồng bộ ngay dữ liệu lãi suất tiền gửi từ 24HMoney vào bảng bank_interest_rates.

Usage:
    py scripts/sync_24hmoney.py            # chỉ sync nếu quá hạn TTL (mặc định 24h)
    py scripts/sync_24hmoney.py --force     # bắt buộc sync ngay (1 request duy nhất tới 24hmoney)

An toàn: nếu nguồn lỗi / parse thiếu → giữ nguyên dữ liệu cũ.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db import SessionLocal  # noqa: E402
from app.services import bank_rate_sync as syn  # noqa: E402


def main() -> None:
    force = "--force" in sys.argv or "-f" in sys.argv
    db = SessionLocal()
    try:
        result = syn.sync(db, force=force)
        print(result)
        if not result.get("ok"):
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
