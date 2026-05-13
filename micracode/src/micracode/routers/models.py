from __future__ import annotations

from fastapi import APIRouter

from micracode_core.model_catalog import list_catalog

from ..config import get_settings

router = APIRouter()


@router.get("/models")
async def models() -> dict:
    return await list_catalog(get_settings())
