from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from micracode_core.engine import MicracodeEngine
from micracode_core.storage import Storage

from .config import get_settings


@lru_cache(maxsize=1)
def _get_engine() -> MicracodeEngine:
    return MicracodeEngine(get_settings())


def get_engine() -> MicracodeEngine:
    return _get_engine()


def get_storage() -> Storage:
    return _get_engine().storage


EngineDep = Annotated[MicracodeEngine, Depends(get_engine)]
StorageDep = Annotated[Storage, Depends(get_storage)]


def reset_deps_cache() -> None:
    _get_engine.cache_clear()
