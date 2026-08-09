from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_roles
from app.db import get_db
from app.models import RoleEnum, SupportReply, SupportTicket, TicketStatusEnum, User
from app.schemas import TicketCreate, TicketOut, TicketReplyCreate

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=list[TicketOut])
def list_tickets(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role in [RoleEnum.support, RoleEnum.admin, RoleEnum.super_admin, RoleEnum.content_manager]:
        # Content-manager xem được toàn bộ yêu cầu hỗ trợ (chỉ xem)
        return db.query(SupportTicket).all()
    return db.query(SupportTicket).filter(SupportTicket.user_id == user.id).all()


@router.post("", response_model=TicketOut)
def create_ticket(payload: TicketCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ticket = SupportTicket(subject=payload.subject, message=payload.message, user_id=user.id)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/replies")
def create_reply(
    ticket_id: int,
    payload: TicketReplyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    # Chỉ chủ ticket hoặc support/admin/super-admin được phản hồi; content-manager chỉ xem
    if user.id != ticket.user_id and user.role not in (RoleEnum.support, RoleEnum.admin, RoleEnum.super_admin):
        raise HTTPException(status_code=403, detail="Bạn không có quyền phản hồi yêu cầu hỗ trợ này.")
    reply = SupportReply(ticket_id=ticket_id, user_id=user.id, message=payload.message)
    db.add(reply)
    db.commit()
    return {"message": "Reply added"}


@router.post("/{ticket_id}/assign", response_model=TicketOut)
def assign_ticket(
    ticket_id: int,
    assignee_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.support, RoleEnum.admin, RoleEnum.super_admin)),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.assignee_id = assignee_id
    ticket.status = TicketStatusEnum.assigned
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/resolve", response_model=TicketOut)
def resolve_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.support, RoleEnum.admin, RoleEnum.super_admin)),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.status = TicketStatusEnum.resolved
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/close", response_model=TicketOut)
def close_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(RoleEnum.support, RoleEnum.admin, RoleEnum.super_admin)),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.status = TicketStatusEnum.closed
    db.commit()
    db.refresh(ticket)
    return ticket
