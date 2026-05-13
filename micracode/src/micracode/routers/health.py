from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    environment: str
    provider: str
    model: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        provider=settings.llm_provider,
        model=settings.active_model,
    )
