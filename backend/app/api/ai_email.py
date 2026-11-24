from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.ai_email import analyze_email, classify_email, suggest_reply


router = APIRouter(prefix="/ai-email", tags=["AI Email Manager"])


class EmailAnalysisRequest(BaseModel):
    message: dict[str, Any] = Field(default_factory=dict)


class ReplyRequest(BaseModel):
    message: dict[str, Any] = Field(default_factory=dict)
    tone: str = Field(default="Professional", max_length=40)
    instructions: str = Field(default="", max_length=3000)


@router.post("/classify")
def classify_message(
    request: EmailAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not request.message:
        raise HTTPException(status_code=400, detail="An email message is required.")
    return classify_email(db, current_user, request.message)


@router.post("/analyze")
def analyze_message(
    request: EmailAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not request.message:
        raise HTTPException(status_code=400, detail="An email message is required.")
    return analyze_email(db, current_user, request.message)


@router.post("/suggest-reply")
def create_reply_suggestion(
    request: ReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not request.message:
        raise HTTPException(status_code=400, detail="An email message is required.")
    return suggest_reply(
        db,
        current_user,
        request.message,
        tone=request.tone,
        instructions=request.instructions,
    )
