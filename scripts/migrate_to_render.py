"""Migrate dữ liệu từ SQLite local (tunix.db) lên Render PostgreSQL.

Cách dùng (chạy 1 lần):
  1. Lấy "External Database URL" của tunix-db trên Render Dashboard
     (Service tunix-db -> Connect -> External Database URL).
  2. Chạy:
       $env:TARGET_DATABASE_URL="postgresql://USER:PASS@host/db?sslmode=require"
       .venv/Scripts/python.exe scripts/migrate_to_render.py
     (hoặc truyền URL làm đối số đầu tiên)

Chỉ chạy khi DB đích CHƯA có user (nếu đã có sẽ bỏ qua để tránh trùng dữ liệu).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models as m

SOURCE_SQLITE = os.environ.get("SOURCE_SQLITE", "tunix.db")
TARGET_URL = os.environ.get("TARGET_DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")


def reset_sequences(engine, table_name: str, id_col: str = "id") -> None:
    """Đồng bộ lại sequence sau khi insert id cố định (Postgres)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', '{id_col}'), "
                f"COALESCE((SELECT MAX({id_col}) FROM {table_name}), 1))"
            )
        )


def main() -> None:
    if not TARGET_URL:
        print("❌ Thiếu URL DB đích. Set biến TARGET_DATABASE_URL hoặc truyền làm đối số.")
        sys.exit(1)
    if TARGET_URL.startswith("sqlite"):
        print("❌ TARGET_DATABASE_URL không được là sqlite.")
        sys.exit(1)
    if not Path(SOURCE_SQLITE).exists():
        print(f"❌ Không tìm thấy DB nguồn: {SOURCE_SQLITE}")
        sys.exit(1)

    src_engine = create_engine(f"sqlite:///{SOURCE_SQLITE}")
    tgt_engine = create_engine(TARGET_URL)

    SrcSession = sessionmaker(bind=src_engine)
    TgtSession = sessionmaker(bind=tgt_engine)

    # Tạo bảng nếu chưa có trên đích
    Base.metadata.create_all(bind=tgt_engine)

    ss, ts = SrcSession(), TgtSession()

    try:
        # Bảo vệ: nếu DB đích đã có user -> bỏ qua
        existing_users = ts.query(m.User).count()
        if existing_users > 0:
            print(f"⚠️  DB đích đã có {existing_users} user. Bỏ qua (tránh trùng).")
            print("    Nếu muốn chạy lại từ đầu, hãy xoá dữ liệu trên Render rồi chạy lại.")
            return

        id_map: dict[str, dict[int, int]] = {}

        # ---------- users ----------
        print("→ Đang chuyển users ...")
        uid_map: dict[int, int] = {}
        for u in ss.query(m.User).all():
            nu = m.User(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                hashed_password=u.hashed_password,
                role=u.role,
                plan=u.plan,
                is_active=u.is_active,
                phone=u.phone,
                avatar=u.avatar,
                plan_expires_at=u.plan_expires_at,
                last_login=u.last_login,
                terms_accepted=u.terms_accepted,
                notify_reports=u.notify_reports,
                notify_news=u.notify_news,
                pref_theme=u.pref_theme,
                created_at=u.created_at,
            )
            ts.add(nu)
            uid_map[u.id] = u.id
        ts.flush()
        id_map["users"] = uid_map
        print(f"   ✅ {len(uid_map)} users")

        # ---------- social_accounts ----------
        print("→ Đang chuyển social_accounts ...")
        n = 0
        for r in ss.query(m.SocialAccount).all():
            ts.add(m.SocialAccount(
                id=r.id, user_id=uid_map.get(r.user_id, r.user_id),
                provider=r.provider, provider_user_id=r.provider_user_id,
                provider_email=r.provider_email, created_at=r.created_at, updated_at=r.updated_at,
            ))
            n += 1
        ts.flush()
        print(f"   ✅ {n} social_accounts")

        # ---------- news ----------
        print("→ Đang chuyển news ...")
        n = 0
        for r in ss.query(m.News).all():
            author_id = uid_map.get(r.author_id) if r.author_id is not None else None
            ts.add(m.News(
                id=r.id, title=r.title, summary=r.summary, content=r.content,
                category=r.category, image_url=r.image_url, author_name=r.author_name,
                image_caption=r.image_caption, tags=r.tags, source=r.source,
                source_url=r.source_url, views=r.views, author_id=author_id,
                status=r.status, created_at=r.created_at,
            ))
            n += 1
        ts.flush()
        print(f"   ✅ {n} news")

        # ---------- faqs ----------
        print("→ Đang chuyển faqs ...")
        n = 0
        for r in ss.query(m.FAQ).all():
            ts.add(m.FAQ(id=r.id, question=r.question, answer=r.answer, category=r.category, is_visible=r.is_visible))
            n += 1
        ts.flush()
        print(f"   ✅ {n} faqs")

        # ---------- contact_messages ----------
        print("→ Đang chuyển contact_messages ...")
        n = 0
        for r in ss.query(m.ContactMessage).all():
            ts.add(m.ContactMessage(
                id=r.id, full_name=r.full_name, email=r.email, phone=r.phone,
                subject=r.subject, message=r.message, is_read=r.is_read, created_at=r.created_at,
            ))
            n += 1
        ts.flush()
        print(f"   ✅ {n} contact_messages")

        # ---------- watchlist ----------
        print("→ Đang chuyển watchlist ...")
        n = 0
        for r in ss.query(m.Watchlist).all():
            ts.add(m.Watchlist(id=r.id, user_id=uid_map.get(r.user_id, r.user_id), symbol=r.symbol, created_at=r.created_at))
            n += 1
        ts.flush()
        print(f"   ✅ {n} watchlist")

        # ---------- plans ----------
        print("→ Đang chuyển plans ...")
        n = 0
        for r in ss.query(m.Plan).all():
            ts.add(m.Plan(id=r.id, name=r.name, tier=r.tier, price=r.price, is_active=r.is_active))
            n += 1
        ts.flush()
        print(f"   ✅ {n} plans")

        # ---------- features ----------
        print("→ Đang chuyển features ...")
        n = 0
        for r in ss.query(m.Feature).all():
            ts.add(m.Feature(id=r.id, key=r.key, name=r.name, description=r.description, access_level=r.access_level, enabled=r.enabled))
            n += 1
        ts.flush()
        print(f"   ✅ {n} features")

        # ---------- settings ----------
        print("→ Đang chuyển settings ...")
        n = 0
        for r in ss.query(m.Setting).all():
            ts.add(m.Setting(id=r.id, key=r.key, value=r.value))
            n += 1
        ts.flush()
        print(f"   ✅ {n} settings")

        # ---------- support_tickets ----------
        print("→ Đang chuyển support_tickets ...")
        tid_map: dict[int, int] = {}
        for r in ss.query(m.SupportTicket).all():
            nt = m.SupportTicket(
                id=r.id, subject=r.subject, message=r.message,
                user_id=uid_map.get(r.user_id, r.user_id),
                assignee_id=uid_map.get(r.assignee_id) if r.assignee_id is not None else None,
                status=r.status, created_at=r.created_at,
            )
            ts.add(nt)
            tid_map[r.id] = r.id
        ts.flush()
        id_map["tickets"] = tid_map
        print(f"   ✅ {len(tid_map)} support_tickets")

        # ---------- support_replies ----------
        print("→ Đang chuyển support_replies ...")
        n = 0
        for r in ss.query(m.SupportReply).all():
            ts.add(m.SupportReply(
                id=r.id, ticket_id=tid_map.get(r.ticket_id, r.ticket_id),
                user_id=uid_map.get(r.user_id, r.user_id),
                message=r.message, created_at=r.created_at,
            ))
            n += 1
        ts.flush()
        print(f"   ✅ {n} support_replies")

        ts.commit()

        # Đồng bộ lại sequence cho các bảng có id cố định
        for tbl in ("users", "news", "faqs", "contact_messages", "watchlist",
                    "plans", "features", "settings", "social_accounts",
                    "support_tickets", "support_replies"):
            try:
                reset_sequences(tgt_engine, tbl)
            except Exception:
                pass

        print("\n🎉 Migrate thành công! Dữ liệu đã chuyển lên Render Postgres.")
        print("   Đăng nhập bằng admin@tunix.vn (mật khẩu như trên máy bạn).")
    finally:
        ss.close()
        ts.close()


if __name__ == "__main__":
    main()
