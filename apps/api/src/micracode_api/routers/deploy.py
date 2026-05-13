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
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from ..config import settings
from ..deps import StorageDep
from ..schemas.project import DeploymentRecord, ProjectRecord
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
    target: Literal["production", "preview"] = Field(default="production")


class VercelDeployResponse(BaseModel):
    id: str
    url: str
    alias_url: str | None = None
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


def _require_token() -> str:
    token = settings.vercel_token.strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="VERCEL_TOKEN is not configured on the API server",
        )
    return token


def _vercel_params() -> dict[str, str] | None:
    team_id = settings.vercel_team_id.strip()
    return {"teamId": team_id} if team_id else None


def _full_url(raw: str) -> str:
    if not raw:
        return ""
    return raw if raw.startswith("http") else f"https://{raw}"


def _pick_production_alias(data: dict[str, object]) -> str | None:
    """Extract the stable production alias from a Vercel deploy response.

    Vercel returns ``alias: list[str]`` with every alias that will be
    attached to the deployment (project-wide ``{name}.vercel.app``,
    custom domains, etc.). Shortest entry is the cleanest project alias;
    we cannot synthesise it ourselves because Vercel appends a
    disambiguating suffix when the bare name is already taken.
    """
    raw = data.get("alias")
    if not isinstance(raw, list):
        return None
    candidates = [a for a in raw if isinstance(a, str) and a]
    if not candidates:
        return None
    candidates.sort(key=len)
    return _full_url(candidates[0])


@router.post("/{project_id}/deploy/vercel", response_model=VercelDeployResponse)
async def deploy_to_vercel(
    project_id: SlugPath,
    body: VercelDeployRequest,
    storage: StorageDep,
) -> VercelDeployResponse:
    token = _require_token()
    project = storage.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # Pin the Vercel project name on first deploy and reuse it forever
    # after — that's what keeps the production alias stable across
    # versions.
    vercel_name = project.vercel_project_name or project_id

    proj_dir = str(storage.project_dir(project_id))
    files = _collect_files(proj_dir)
    if not files:
        raise HTTPException(status_code=400, detail="project has no files to deploy")

    payload = {
        "name": vercel_name,
        "files": files,
        "projectSettings": {"framework": "nextjs"},
        "target": body.target,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            res = await client.post(
                VERCEL_API, json=payload, headers=headers, params=_vercel_params()
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"vercel request failed: {exc}") from exc

    if res.status_code >= 400:
        try:
            err = res.json().get("error", {}).get("message") or res.text
        except ValueError:
            err = res.text
        raise HTTPException(status_code=res.status_code, detail=f"vercel: {err}")

    data = res.json()
    permalink = _full_url(data.get("url") or "")
    alias_url = _pick_production_alias(data) if body.target == "production" else None

    record = DeploymentRecord(
        id=data.get("id", ""),
        url=permalink,
        alias_url=alias_url,
        target=body.target,
        created_at=datetime.now(UTC),
        is_current_production=(body.target == "production"),
    )
    storage.add_deployment(project_id, record, vercel_project_name=vercel_name)

    return VercelDeployResponse(
        id=record.id,
        url=record.url,
        alias_url=alias_url,
        inspector_url=data.get("inspectorUrl"),
    )


class PromoteResponse(BaseModel):
    ok: bool
    project: ProjectRecord


@router.post(
    "/{project_id}/deployments/{deployment_id}/promote",
    response_model=PromoteResponse,
)
async def promote_deployment(
    project_id: SlugPath,
    deployment_id: str,
    storage: StorageDep,
) -> PromoteResponse:
    """Point the production alias at a previously created deployment.

    Vercel keeps every deployment online forever; promoting just shifts
    the shared ``{name}.vercel.app`` alias. Cheap, instant, no rebuild.
    """
    token = _require_token()
    project = storage.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.vercel_project_name:
        raise HTTPException(status_code=400, detail="project has not been deployed yet")
    if not any(d.id == deployment_id for d in project.deployments):
        raise HTTPException(status_code=404, detail="deployment not found")

    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"https://api.vercel.com/v10/projects/"
        f"{project.vercel_project_name}/promote/{deployment_id}"
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            res = await client.post(url, headers=headers, params=_vercel_params())
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"vercel request failed: {exc}") from exc

    # Vercel returns 201 on success; some endpoints return 200.
    if res.status_code >= 400:
        try:
            err = res.json().get("error", {}).get("message") or res.text
        except ValueError:
            err = res.text
        raise HTTPException(status_code=res.status_code, detail=f"vercel: {err}")

    try:
        updated = storage.set_current_production(project_id, deployment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="deployment not found") from exc

    return PromoteResponse(ok=True, project=updated)


async def delete_vercel_project(project_name: str) -> None:
    """Delete a project on Vercel. Idempotent — 404 is treated as success."""
    token = _require_token()
    url = f"https://api.vercel.com/v9/projects/{project_name}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.delete(url, headers=headers, params=_vercel_params())
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"vercel request failed: {exc}"
            ) from exc
    if res.status_code == 404:
        return
    if res.status_code >= 400:
        try:
            err = res.json().get("error", {}).get("message") or res.text
        except ValueError:
            err = res.text
        raise HTTPException(status_code=res.status_code, detail=f"vercel: {err}")
