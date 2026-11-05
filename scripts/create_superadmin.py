"""Tạo Super Admin đầu tiên (chạy 1 lần từ CLI).

Usage:
    python scripts/create_superadmin.py --email admin@tunix.vn --password "matkhau" [--name "Admin"] [--phone 0912345678]

Lưu ý: chạy với cùng môi trường Python dùng để chạy server (để dùng đúng DATABASE_URL).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db import SessionLocal
from app.models import PlanTierEnum, RoleEnum, User


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo Super Admin VinaFintech")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Super Admin")
    parser.add_argument("--phone", default=None)
    args = parser.parse_args()

    if len(args.password) < 6:
        print("❌ Mật khẩu phải có ít nhất 6 ký tự.")
        sys.exit(1)

    db: Session = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email.lower()).first()
        if existing:
            if existing.role != RoleEnum.super_admin:
                existing.role = RoleEnum.super_admin
                existing.is_active = True
                existing.email_verified = True
                db.commit()
                print(f"✅ Đã nâng quyền {args.email} thành Super Admin.")
            else:
                print(f"ℹ️ {args.email} đã là Super Admin.")
            return

        user = User(
            email=args.email.lower(),
            full_name=args.name,
            phone=args.phone,
            hashed_password=get_password_hash(args.password),
            role=RoleEnum.super_admin,
            plan=PlanTierEnum.pro,
            is_active=True,
            terms_accepted=True,
            email_verified=True,  # Super Admin không cần xác thực email
        )
        db.add(user)
        db.commit()
        print(f"✅ Đã tạo Super Admin: {args.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
