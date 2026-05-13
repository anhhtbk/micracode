"""Vercel deployment endpoint.

Reads a project's source tree (same exclusion rules as the download
endpoint), encodes every file inline, and ships the bundle to the
Vercel v13 deployments API on behalf of the caller's personal token.

The user supplies their own VERCEL_TOKEN per request; we do not store
it. This keeps the local-first, no-secrets-on-disk posture intact.
"""

from __future__ import annotations

import base64
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from ..config import settings
from ..deps import StorageDep
from ..storage import SLUG_RE, iter_ignored_top_level

router = APIRouter(prefix="/projects")

SlugPath = Annotated[
    str,
    Path(pattern=SLUG_RE.pattern, min_length=1, max_length=63, description="Project slug."),
]

VERCEL_API = "https://api.vercel.com/v13/deployments"
# Vercel rejects single deployments larger than this on the inline-file path.
# Keep a generous ceiling but bail early with a clear error.
_MAX_BUNDLE_BYTES = 100 * 1024 * 1024

# Block secret-bearing files from being uploaded into Vercel's build context.
# Env vars must be configured via the Vercel dashboard, not bundled in source.
_SECRET_FILENAMES: frozenset[str] = frozenset({".env", ".env.local"})
_SECRET_NAME_PREFIXES: tuple[str, ...] = (".env.",)
_SECRET_SUFFIXES: tuple[str, ...] = (".pem", ".key", ".p12", ".pfx")


def _is_secret_like(name: str) -> bool:
    if name in _SECRET_FILENAMES:
        return True
    if name.startswith(_SECRET_NAME_PREFIXES):
        return True
    return name.endswith(_SECRET_SUFFIXES)


class VercelDeployRequest(BaseModel):
    name: str | None = Field(default=None, description="Override deployment name.")
    target: str | None = Field(default="production", description="'production' or 'preview'.")


class VercelDeployResponse(BaseModel):
    id: str
    url: str
    inspector_url: str | None = None


def _collect_files(proj_dir: str) -> list[dict[str, str]]:
    ignored = frozenset(iter_ignored_top_level())
    files: list[dict[str, str]] = []
    blocked: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(proj_dir, followlinks=False):
        rel_dir = os.path.relpath(dirpath, proj_dir)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d not in ignored]
            rel_prefix = ""
        else:
            rel_prefix = rel_dir.replace(os.sep, "/")
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            if os.path.islink(abs_path):
                continue
            rel_path = f"{rel_prefix}/{name}" if rel_prefix else name
            if _is_secret_like(name):
                blocked.append(rel_path)
                continue
            with open(abs_path, "rb") as fh:
                raw = fh.read()
            total += len(raw)
            if total > _MAX_BUNDLE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"project exceeds {_MAX_BUNDLE_BYTES // (1024 * 1024)}MB deploy limit",
                )
            files.append(
                {
                    "file": rel_path,
                    "data": base64.b64encode(raw).decode("ascii"),
                    "encoding": "base64",
                }
            )
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=(
                "refusing to deploy secret-bearing files; remove them or "
                "configure env vars on Vercel: " + ", ".join(sorted(blocked))
            ),
        )
    return files


@router.post("/{project_id}/deploy/vercel", response_model=VercelDeployResponse)
async def deploy_to_vercel(
    project_id: SlugPath,
    body: VercelDeployRequest,
    storage: StorageDep,
) -> VercelDeployResponse:
    token = settings.vercel_token.strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="VERCEL_TOKEN is not configured on the API server",
        )

    if storage.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    proj_dir = str(storage.project_dir(project_id))
    files = _collect_files(proj_dir)
    if not files:
        raise HTTPException(status_code=400, detail="project has no files to deploy")

    payload = {
        "name": (body.name or project_id),
        "files": files,
        "projectSettings": {"framework": "nextjs"},
        "target": body.target or "production",
    }

    team_id = settings.vercel_team_id.strip()
    params = {"teamId": team_id} if team_id else None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            res = await client.post(VERCEL_API, json=payload, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"vercel request failed: {exc}") from exc

    if res.status_code >= 400:
        try:
            err = res.json().get("error", {}).get("message") or res.text
        except ValueError:
            err = res.text
        raise HTTPException(status_code=res.status_code, detail=f"vercel: {err}")

    data = res.json()
    url = data.get("url") or ""
    return VercelDeployResponse(
        id=data.get("id", ""),
        url=f"https://{url}" if url and not url.startswith("http") else url,
        inspector_url=data.get("inspectorUrl"),
    )
