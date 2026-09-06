"""Test đơn vị cho logic Lãi ngân hàng (Giai đoạn 1) — chạy: python scripts/test_bank_rate.py

Test các hàm thuần + service (cần DB SQLite tạm). Dùng sqlite trong-memory.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services import bank_rate_service as svc

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def approx(a, b, tol=1):
    return abs(a - b) <= tol


def test_calc_interest():
    print("calc_interest")
    # Ví dụ mẫu: 500tr, 12 tháng, 7%/năm → lãi 35tr, tổng 535tr
    r = svc.calc_interest(500_000_000, 7.0, 12)
    check("vd 500tr/12th/7% lãi 35tr", r["interest"] == 35_000_000, r)
    check("vd 500tr/12th/7% tổng 535tr", r["total"] == 535_000_000, r)

    # 100tr, 6 tháng, 8%/năm → lãi 4tr (100*0.08*0.5)
    r2 = svc.calc_interest(100_000_000, 8.0, 6)
    check("100tr/6th/8% lãi 4tr", r2["interest"] == 4_000_000, r2)

    # 1 tỷ, 1 tháng, 12%/năm → lãi 10tr
    r3 = svc.calc_interest(1_000_000_000, 12.0, 1)
    check("1ty/1th/12% lãi 10tr", r3["interest"] == 10_000_000, r3)

    # 0 / âm
    r4 = svc.calc_interest(0, 7.0, 12)
    check("amount=0 -> interest 0", r4["interest"] == 0 and r4["total"] == 0, r4)
    r5 = svc.calc_interest(-5, 7.0, 12)
    check("amount âm -> interest 0", r5["interest"] == 0, r5)


def test_db_roundtrip():
    print("service/db")
    # DB tạm file
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)
        db = TestSession()
        n = svc.seed_rates(db)
        check("seed thêm dòng >0", n > 0, n)
        check("seed lần 2 idempotent (0)", svc.seed_rates(db) == 0)

        m = svc.meta(db)
        check("meta banks >=5", len(m["banks"]) >= 5, m["banks"])
        check("meta terms == [1,3,6,12,18,24]", m["terms"] == [1, 3, 6, 12, 18, 24])
        check("meta có online+counter", set(m["channels"]) == {"online", "counter"})

        rows = svc.list_rates(db, channel="online", term=12)
        check("list online 12th == số bank", len(rows) == len(m["banks"]), len(rows))

        est = svc.estimate(db, 500_000_000, "VCB", 12, "online")
        check("estimate found", est["found"], est)
        if est["found"]:
            rate = est["interest_rate"]
            calc = svc.calc_interest(500_000_000, rate, 12)
            check("estimate lãi khớp calc", est["expected_interest"] == calc["interest"], est)

        est_missing = svc.estimate(db, 500_000_000, "XXX", 12, "online")
        check("estimate bank lạ -> not found", est_missing["found"] is False)

        cmp = svc.compare(db, 100_000_000, 12, "online")
        check("compare có items", cmp["count"] >= 5, cmp["count"])
        if cmp["items"]:
            rates = [i["interest_rate"] for i in cmp["items"]]
            check("compare sort giảm dần", rates == sorted(rates, reverse=True), rates)
        db.close()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    test_calc_interest()
    test_db_roundtrip()
    print(f"\n== {PASS} passed, {FAIL} failed ==")
    sys.exit(1 if FAIL else 0)
