"""Đẩy TOÀN BỘ database local (tunix.db - SQLite) lên PostgreSQL của Render.

Khác `migrate_to_render.py` (bản cũ chỉ chuyển 11 bảng và tự bỏ qua nếu DB đích
đã có user), script này:
  - copy ĐỦ mọi bảng đang có trong SQLite (kể cả bảng mới: signal_*, admin_logs,
    alerts, ai_conversations, member_quota, tool_files, cash_transactions...);
  - giữ NGUYÊN khoá chính (id) nên mọi khoá ngoại vẫn đúng, không cần remap;
  - có 2 chế độ: `replace` (DB đích = bản sao của local) và `merge` (chỉ thêm
    bản ghi chưa có, giữ dữ liệu đang có trên Render);
  - đồng bộ lại sequence (serial) sau khi insert id cố định, nếu không lần
    INSERT tiếp theo của app sẽ lỗi "duplicate key";
  - in bảng đối chiếu số dòng nguồn/đích sau khi chạy.

CÁCH DÙNG
  Cách 1 — chạy trực tiếp, script TỰ HỎI URL (ký tự ẩn, không lưu ra file):
         py scripts/push_db_to_render.py --mode merge
  Cách 2 — dùng biến môi trường trong phiên (không lưu ra file):
         $env:TARGET_DATABASE_URL="postgresql://..."; py scripts/push_db_to_render.py --mode merge
  Cách 3 — lưu vào file `.target_db_url` (đã gitignore) ở gốc project, mỗi dòng 1 URL.

  ⚠️ TUYỆT ĐỐI KHÔNG thêm TARGET_DATABASE_URL vào `.env`: app dùng pydantic Settings với
     `extra='forbid'` nên `.env` có key lạ sẽ làm backend KHÔNG khởi động được
     (ValidationError: extra_forbidden).

  URL lấy ở: Render Dashboard -> service `Tunix-test1-db` -> Connect -> **External Database URL**
  (dạng dpg-xxxx-a.singapore-postgres.render.com, KHÔNG dùng Internal URL).

  Xem trước (không cần URL): py scripts/push_db_to_render.py --dry-run

  LƯU Ý: dùng interpreter CÓ sqlalchemy + psycopg2 — venv của workspace KHÔNG có, nên chạy bằng
  `py` (Python toàn cục) hoặc `.venv` của workspace 2.TuniX-Product.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Console Windows mặc định là cp1252 → in tiếng Việt/mũi tên sẽ UnicodeEncodeError.
# Ép stdout/stderr sang UTF-8 để script chạy được từ PowerShell/cmd.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — Python cũ / stream bị redirect
        pass

from sqlalchemy import LargeBinary, MetaData, create_engine, func, inspect, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.exc import InterfaceError, OperationalError  # noqa: E402

from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402  (import để đăng ký toàn bộ model)

SOURCE_SQLITE = ROOT / "tunix.db"
CHUNK_SIZE = 200
_SKIP_TABLES = {"sqlite_sequence", "spatial_ref_sys"}


def mask_url(url: str) -> str:
    """Che mật khẩu khi in ra log."""
    if "://" not in url:
        return url
    head, rest = url.split("://", 1)
    if "@" in rest:
        rest = "***:***@" + rest.split("@", 1)[1]
    return f"{head}://{rest}"


def load_target_url(cli_url: str | None) -> str:
    """Ưu tiên --url > env > file `.target_db_url` > hỏi trực tiếp (ẩn ký tự)."""
    if cli_url and cli_url.strip():
        return cli_url.strip()
    env_value = os.environ.get("TARGET_DATABASE_URL", "").strip()
    if env_value:
        return env_value
    url_file = ROOT / ".target_db_url"
    if url_file.exists():
        for line in url_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line

    # Cảnh báo (KHÔNG dùng) nếu người dùng lỡ dán URL vào .env — vì app dùng pydantic
    # Settings với extra='forbid', .env có key lạ sẽ làm backend KHÔNG khởi động được.
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("TARGET_DATABASE_URL"):
                print("⚠️  Phát hiện TARGET_DATABASE_URL trong .env — HÃY XOÁ dòng đó!")
                print("   App dùng pydantic Settings (extra='forbid') nên .env có key lạ sẽ làm")
                print("   backend KHÔNG khởi động được (ValidationError extra_forbidden).")
                print("   Thay vào đó: dán URL khi script hỏi, hoặc lưu vào `.target_db_url`, hoặc dùng biến môi trường.")
                break
    # Không tìm thấy → hỏi trực tiếp (ẩn ký tự) để không phải lưu mật khẩu vào file.
    if sys.stdin.isatty():
        import getpass
        try:
            typed = getpass.getpass(
                "Dán 'External Database URL' của Render (ký tự sẽ bị ẩn): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        if typed:
            print("   (đã nhận URL, sẽ không lưu vào file nào)")
        return typed
    return ""


def reset_sequences(engine, tables: list[str]) -> None:
    """setval cho mọi bảng có cột id kiểu serial (bỏ qua bảng không có)."""
    for name in tables:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('\"{name}\"', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM \"{name}\"), 1))"
                    )
                )
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Đẩy toàn bộ SQLite local lên Render PostgreSQL")
    parser.add_argument("--url", default=None, help="External Database URL của Render")
    parser.add_argument("--source", default=str(SOURCE_SQLITE), help="File SQLite nguồn")
    parser.add_argument("--mode", choices=["replace", "merge"], default="replace",
                        help="replace = xoá dữ liệu đích rồi copy đè (mặc định); merge = chỉ thêm bản ghi chưa có")
    parser.add_argument("--yes", action="store_true", help="Không hỏi xác nhận (dùng khi chạy tự động)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in danh sách bảng + số dòng, không ghi")
    parser.add_argument("--allow-any-host", action="store_true", help="Cho phép host không phải *.render.com")
    parser.add_argument("--skip", default="", help="Danh sách bảng bỏ qua, cách nhau dấu phẩy (vd: tool_files,tmp_x)")
    parser.add_argument("--keep-blobs", action="store_true",
                        help="Vẫn đẩy các bảng có BLOB lớn (mặc định bỏ qua vì dễ làm Render cắt SSL)")
    parser.add_argument("--max-blob-mb", type=float, default=8.0,
                        help="Ngưỡng BLOB tối đa (MB) cho 1 bảng; quá ngưỡng sẽ bị bỏ qua (mặc định 8)")
    args = parser.parse_args()

    target_url = load_target_url(args.url)
    if not target_url and args.dry_run:
        # dry-run chỉ đọc nguồn nên không bắt buộc URL đích.
        target_url = "postgresql://dry-run@dry-run.invalid/tunix"
    if not target_url:
        print("❌ Chưa có URL DB đích.")
        print("   Chạy lại ở terminal thật (không pipe) để script tự hỏi URL, hoặc:")
        print("     $env:TARGET_DATABASE_URL=\"postgresql://...\"; py scripts/push_db_to_render.py --mode merge")
        print("   (lấy ở Render -> Tunix-test1-db -> Connect -> External Database URL;")
        print("    ĐỪNG thêm vào .env vì sẽ làm backend không khởi động được)")
        return 1
    if target_url.startswith("sqlite"):
        print("❌ URL đích không được là sqlite.")
        return 1

    # --- Kiểm tra URL trước khi kết nối ---
    from sqlalchemy.engine import make_url
    try:
        parsed = make_url(target_url)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ URL không hợp lệ: {exc}")
        return 1
    host = parsed.host or ""
    print(f"→ DB nguồn : {args.source}")
    print(f"→ DB đích  : {mask_url(target_url)}")
    if "." not in host or not host.endswith("render.com"):
        if not args.allow_any_host and not args.dry_run:
            print("❌ Hostname đích phải có dạng dpg-<id>-a.<region>-postgres.render.com")
            print("   (copy ĐẦY ĐỦ 'External Database URL' trên Render). Dùng --allow-any-host để bỏ qua.")
            return 1
        print("   ⚠️  Host không phải *.render.com (đã cho phép bằng --allow-any-host).")

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"❌ Không tìm thấy DB nguồn: {source_path}")
        return 1

    src_engine = create_engine(f"sqlite:///{source_path}")
    # Render (Singapore): bật keepalive + recycle để SSL không bị cắt giữa chừng
    # (bảng tool_files chứa file zip ~20MB nên gửi qua mạng khá nặng).
    tgt_engine = create_engine(
        target_url,
        pool_pre_ping=True,
        pool_recycle=90,
        connect_args={
            "connect_timeout": 30,
            "keepalives": 1,
            "keepalives_idle": 20,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )

    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    model_tables = {t.name: t for t in Base.metadata.sorted_tables}

    def _table_for(name: str):
        """Bảng theo model (kiểu dữ liệu chuẩn) nếu có, không thì dùng bảng reflect.

        KHÔNG dùng `a or b` với đối tượng Table: SQLAlchemy ném TypeError
        'Boolean value of this clause is not defined'.
        """
        table = model_tables.get(name)
        return table if table is not None else src_meta.tables[name]

    src_names = [n for n in src_meta.tables if n not in _SKIP_TABLES]
    order = [t.name for t in Base.metadata.sorted_tables if t.name in src_names]
    extra = [n for n in src_names if n not in model_tables]
    order += sorted(extra)

    if args.dry_run:
        print("\n== DRY RUN: bảng sẽ copy (theo thứ tự an toàn khoá ngoại) ==")
        with src_engine.connect() as sc:
            total = 0
            for name in order:
                count = sc.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
                total += count
                print(f"   {name:28} {count:>8}")
            print(f"   {'TỔNG':28} {total:>8}")
        print("\n(chưa ghi gì lên DB đích)")
        return 0

    print(f"\n→ Chế độ: {args.mode.upper()}")
    if args.mode == "replace":
        print("   ⚠️  Sẽ XOÁ toàn bộ dữ liệu các bảng này trên DB đích rồi copy từ local.")
    if not args.yes:
        answer = input("Tiếp tục? (gõ 'yes' để chạy): ").strip().lower()
        if answer != "yes":
            print("Đã huỷ.")
            return 1

    # Tạo schema đích: bảng theo model (đúng kiểu dữ liệu Postgres) + bảng lạ còn thiếu.
    Base.metadata.create_all(bind=tgt_engine)
    for name in extra:
        try:
            src_meta.tables[name].create(bind=tgt_engine, checkfirst=True)
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠️  Không tạo được bảng {name} ở đích: {exc}")

    tgt_inspector = inspect(tgt_engine)
    tgt_existing = set(tgt_inspector.get_table_names())

    # Cột kiểu VARCHAR quá ngắn trên DB đích (Render tạo từ schema cũ) sẽ làm insert lỗi
    # "value too long" — nới sẵn về TEXT. Hiện có: users.avatar (data-URI base64 rất dài).
    for table_name, column in (("users", "avatar"),):
        if table_name in tgt_existing:
            try:
                with tgt_engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column}" TYPE TEXT'))
                print(f"   → Đã nới {table_name}.{column} thành TEXT.")
            except Exception:  # noqa: BLE001 — đã là TEXT thì bỏ qua
                pass

    target_cols: dict[str, list[str]] = {}
    for name in order:
        if name in tgt_existing:
            target_cols[name] = [c["name"] for c in tgt_inspector.get_columns(name)]
        else:
            target_cols[name] = []

    copied_tables: list[str] = []
    report: list[tuple[str, int, int, int]] = []
    skip_names = {s.strip() for s in args.skip.split(",") if s.strip()}

    # --- replace: xoá đích trước, theo thứ tự NGƯỢC (bảng con trước) để không vướng FK ---
    if args.mode == "replace":
        for name in reversed(order):
            if name not in tgt_existing or name in skip_names:
                continue
            try:
                with tgt_engine.begin() as tc:
                    tc.execute(text(f'DELETE FROM "{name}"'))
            except Exception as exc:  # noqa: BLE001
                print(f"   ⚠️  Không xoá được {name}: {str(exc)[:70]}")

    with src_engine.connect() as sc:
        # 2) copy từng bảng, giữ nguyên id. MỖI BẢNG 1 TRANSACTION RIÊNG để 1 bảng lỗi
        #    (hoặc bị Render cắt SSL) không làm mất toàn bộ công sức các bảng trước.
        for name in order:
            tgt_cols = target_cols.get(name) or []
            if not tgt_cols:
                print(f"   ⚠️  Bỏ qua {name}: không có bảng ở đích")
                continue
            # Đọc bằng bảng theo MODEL (đúng kiểu JSON/DateTime/Boolean/Enum) để dữ liệu
            # sang Postgres không bị double-encode; bảng không có model thì dùng bảng reflect.
            src_table = _table_for(name)
            src_cols = [c.name for c in src_table.columns]
            cols = [c for c in src_cols if c in tgt_cols]
            dropped = [c for c in src_cols if c not in tgt_cols]
            if dropped:
                print(f"   ⚠️  {name}: bỏ cột không có ở đích {dropped}")

            tgt_table = _table_for(name)
            use_on_conflict = args.mode == "merge" and len(tgt_table.primary_key.columns) > 0
            has_blob = any(isinstance(c.type, LargeBinary) for c in tgt_table.columns)

            start = time.time()
            src_count = sc.execute(select(func.count()).select_from(src_table)).scalar() or 0

            # Bỏ qua theo yêu cầu, hoặc bỏ qua bảng có BLOB quá lớn (gửi 20MB qua SSL tới Render
            # thường bị cắt kết nối → kéo theo các bảng sau lỗi khoá ngoại).
            if name in skip_names:
                print(f"   ↷ {name:28} bỏ qua theo --skip (nguồn={src_count})")
                report.append((name, src_count, -1, 0))
                continue
            if has_blob and not args.keep_blobs:
                max_blob = 0
                for col in tgt_table.columns:
                    if isinstance(col.type, LargeBinary):
                        try:
                            val = sc.execute(
                                select(func.max(func.length(src_table.c[col.name])))
                            ).scalar()
                            max_blob = max(max_blob, int(val or 0))
                        except Exception:  # noqa: BLE001
                            pass
                if max_blob > args.max_blob_mb * 1024 * 1024:
                    print(f"   ↷ {name:28} bỏ qua: có BLOB {max_blob/1024/1024:.1f} MB "
                          f"> {args.max_blob_mb:.0f} MB (dùng --keep-blobs nếu muốn đẩy)")
                    report.append((name, src_count, -1, 0))
                    continue
            if not cols:
                print(f"   ·  {name:28} bỏ qua (không có cột dùng chung)")
                report.append((name, src_count, 0, 0))
                continue

            insert_stmt = select(*[src_table.c[c] for c in cols])

            def _run_copy(per_row: bool, chunk: int) -> tuple[int, int]:
                """Ghi 1 bảng trong 1 transaction riêng. Trả (số dòng ghi, số dòng lỗi)."""
                written = failed = 0
                with tgt_engine.begin() as tc:
                    def _ins(rows: list[dict]) -> None:
                        stmt = pg_insert(tgt_table).values(rows)
                        if use_on_conflict:
                            stmt = stmt.on_conflict_do_nothing()
                        tc.execute(stmt)

                    for part in sc.execute(insert_stmt).partitions(chunk):
                        if not per_row:
                            rows = [dict(zip(cols, row)) for row in part]
                            if rows:
                                _ins(rows)
                                written += len(rows)
                        else:
                            for row in part:
                                try:
                                    with tc.begin_nested():
                                        _ins([dict(zip(cols, row))])
                                    written += 1
                                except Exception:  # noqa: BLE001 — bỏ qua dòng lỗi, đếm lại
                                    failed += 1
                return written, failed

            written = failed = 0
            done = False
            # BLOB lớn (tool_files) → gửi từng dòng; bảng thường → lô 200 dòng.
            chunk = 1 if has_blob else CHUNK_SIZE
            for attempt in (1, 2):
                try:
                    written, failed = _run_copy(per_row=False, chunk=chunk)
                    done = True
                    break
                except (OperationalError, InterfaceError) as exc:
                    print(f"   ⚠️  {name}: kết nối bị cắt ({str(exc)[:60]}) → thử lại (lần {attempt})")
                    tgt_engine.dispose()
                    time.sleep(2)
                except Exception as exc:  # noqa: BLE001 — lỗi dữ liệu → ghi từng dòng
                    print(f"   ⚠️  {name}: lỗi khi ghi lô ({str(exc)[:70]}) → ghi từng dòng")
                    tgt_engine.dispose()
                    try:
                        written, failed = _run_copy(per_row=True, chunk=chunk)
                        done = True
                    except Exception as exc2:  # noqa: BLE001
                        print(f"   ❌ {name}: bỏ qua bảng này ({str(exc2)[:70]})")
                    break
            if not done:
                print(f"   ❌ {name}: KHÔNG ghi được (đã thử 2 lần)")
                report.append((name, src_count, 0, src_count))
                continue

            try:
                with tgt_engine.connect() as tcc:
                    tgt_count = tcc.execute(select(func.count()).select_from(tgt_table)).scalar() or 0
            except Exception:  # noqa: BLE001
                tgt_count = written
            report.append((name, src_count, tgt_count, failed))
            flag = "✅" if failed == 0 and tgt_count >= src_count else "⚠️ "
            extra_txt = f" lỗi={failed}" if failed else ""
            print(f"   {flag} {name:28} nguồn={src_count:>7}  đích={tgt_count:>7}{extra_txt}  ({time.time()-start:4.1f}s)")
            copied_tables.append(name)

    reset_sequences(tgt_engine, copied_tables)

    print("\n== Đối chiếu ==")
    skipped = [(n, s) for n, s, t, f in report if t == -1]
    mismatch = [(n, s, t, f) for n, s, t, f in report if t != -1 and (t < s or f)]
    print(f"   {len(report)} bảng | bỏ qua: {len(skipped)} | khớp: {len(report) - len(mismatch) - len(skipped)} "
          f"| thiếu/lỗi: {len(mismatch)}")
    for n, s in skipped:
        print(f"     ↷ {n}: bỏ qua (nguồn {s} dòng)")
    for n, s, t, f in mismatch:
        detail = f"lỗi {f} dòng" if f else "thiếu dòng"
        print(f"     ⚠️  {n}: nguồn {s} > đích {t} ({detail})")
    if mismatch and args.mode == "merge":
        print("   (merge chỉ thêm bản ghi chưa có — chênh lệch là bình thường nếu đích đã có dữ liệu khác)")

    print("\n🎉 Đẩy database lên Render xong.")
    print("   Render sẽ tự khởi động lại service khi có request; mở web kiểm tra dữ liệu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
