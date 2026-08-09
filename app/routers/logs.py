from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import require_roles
from app.db import get_db
from app.models import Log, RoleEnum, User

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
def list_logs(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.super_admin)),
):
    rows = db.query(Log).order_by(Log.id.desc()).limit(200).all()
    return [
        {"id": row.id, "level": row.level, "message": row.message, "created_at": row.created_at.isoformat()}
        for row in rows
    ]
