from datetime import datetime, timezone
import httpx

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user_ai_settings import UserAISettings
from app.models.user import User
from app.services.ai_crypto import encrypt_api_key
from app.services.ai_email import list_supported_gemini_models


router = APIRouter(prefix="/ai-settings", tags=["AI Settings"])

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiConnectRequest(BaseModel):
    api_key: str = Field(min_length=20, max_length=512)
    privacy_acknowledged: bool = False


def _user_gemini_settings(db: Session, user: User):
    return (
        db.query(UserAISettings)
        .filter(
            UserAISettings.user_id == user.id,
            UserAISettings.provider == "gemini",
        )
        .first()
    )


@router.get("/gemini")
def gemini_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    config = _user_gemini_settings(db, user)
    return {
        "configured": bool(config and config.is_active),
        "provider": "gemini",
        "model": config.model_name if config and config.is_active else None,
    }


@router.post("/gemini/connect")
def connect_gemini(
    payload: GeminiConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="Gemini API key is required.")
    if not payload.privacy_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Please acknowledge Google's data-processing notice before connecting.",
        )

    candidates = list_supported_gemini_models(api_key)
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="The API key is valid, but Google returned no available Gemini Flash-Lite model supporting content generation.",
        )

    model_name = candidates[0]["name"].removeprefix("models/")

    config = _user_gemini_settings(db, user)
    if config is None:
        config = UserAISettings(
            user_id=user.id,
            provider="gemini",
            encrypted_api_key=encrypt_api_key(api_key),
            model_name=model_name,
            is_active=True,
            privacy_acknowledged_at=datetime.now(timezone.utc),
        )
        db.add(config)
    else:
        config.encrypted_api_key = encrypt_api_key(api_key)
        config.model_name = model_name
        config.is_active = True
        config.privacy_acknowledged_at = datetime.now(timezone.utc)

    db.commit()
    return {
        "connected": True,
        "provider": "gemini",
        "model": model_name,
    }


@router.delete("/gemini", status_code=status.HTTP_200_OK)
def disconnect_gemini(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    config = _user_gemini_settings(db, user)
    if config is not None:
        db.delete(config)
        db.commit()
    return {"connected": False, "provider": "gemini"}
