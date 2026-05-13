"""Local-filesystem storage for projects, generated code, and chat history."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import TypeAdapter

from .schemas.project import ProjectRecord, PromptRecord, PromptRole, SnapshotRecord
from .starter.next_default import NEXT_STARTER_FILES

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

SIDECAR_DIR = ".micracode"
PROJECT_FILE = "project.json"
PROMPTS_FILE = "prompts.jsonl"
SNAPSHOTS_DIR = "snapshots"
SNAPSHOT_FILES_DIR = "files"
SNAPSHOT_META_FILE = "project.json"

SNAPSHOT_KEEP = 20

SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")

_IGNORED_TOP_LEVEL: frozenset[str] = frozenset(
    {SIDECAR_DIR, "node_modules", ".git", ".next", ".turbo", "dist", ".cache"}
)

_project_adapter = TypeAdapter(ProjectRecord)
_prompt_adapter = TypeAdapter(PromptRecord)
_snapshot_adapter = TypeAdapter(SnapshotRecord)


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    cleaned = cleaned[:63]
    if not cleaned or not cleaned[0].isalnum():
        return ""
    return cleaned


def safe_join(root: Path, rel: str | os.PathLike[str]) -> Path:
    """Resolve *rel* against *root*, blocking traversal + absolute paths."""
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {rel!r}")

    root_resolved = root.resolve(strict=False)
    candidate = (root / rel_path).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {rel!r}") from exc
    return candidate


class Storage:
    """Stateless-ish helper bound to a single root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self._write_lock = Lock()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, slug: str) -> Path:
        self._validate_slug(slug)
        return self.root / slug

    def sidecar_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / SIDECAR_DIR

    @staticmethod
    def _validate_slug(slug: str) -> None:
        if not SLUG_RE.fullmatch(slug):
            raise ValueError(f"invalid project id: {slug!r}")

    def unique_slug(self, name: str) -> str:
        base = slugify(name)
        if not base:
            base = f"project-{uuid.uuid4().hex[:8]}"
        candidate = base
        n = 2
        while (self.root / candidate).exists():
            suffix = f"-{n}"
            candidate = f"{base[: 63 - len(suffix)]}{suffix}"
            n += 1
        return candidate

    def create_project(self, name: str, template: str = "next") -> ProjectRecord:
        self.ensure_root()
        slug = self.unique_slug(name)
        proj = self.root / slug
        sidecar = proj / SIDECAR_DIR
        sidecar.mkdir(parents=True, exist_ok=False)

        now = _now()
        record = ProjectRecord(
            id=slug,
            name=name.strip(),
            template=template,
            created_at=now,
            updated_at=now,
        )
        self._write_project_json(slug, record)
        (sidecar / PROMPTS_FILE).touch()

        if template == "next":
            for rel, content in NEXT_STARTER_FILES.items():
                self.write_file(slug, rel, content)
            refreshed = self._try_read_project_json(slug)
            if refreshed is not None:
                record = refreshed

        return record

    def ensure_next_preview_layout(self, slug: str) -> None:
        rec = self._try_read_project_json(slug)
        if rec is None or rec.template != "next":
            return
        proj = self.project_dir(slug)
        if not proj.exists():
            return
        for rel, content in NEXT_STARTER_FILES.items():
            if safe_join(proj, rel).is_file():
                continue
            self.write_file(slug, rel, content)
        self._ensure_package_json_dev_script(slug)
        self._ensure_starter_dependencies(slug)

    def _ensure_package_json_dev_script(self, slug: str) -> None:
        pkg_path = safe_join(self.project_dir(slug), "package.json")
        if not pkg_path.is_file():
            return
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            scripts = {}
            data["scripts"] = scripts
        dev = scripts.get("dev")
        if isinstance(dev, str) and dev.strip():
            return
        scripts["dev"] = "next dev --hostname 0.0.0.0 --port 3000"
        self.write_file(slug, "package.json", json.dumps(data, indent=2) + "\n")

    def _ensure_starter_dependencies(self, slug: str) -> None:
        pkg_path = safe_join(self.project_dir(slug), "package.json")
        if not pkg_path.is_file():
            return

        try:
            starter = json.loads(NEXT_STARTER_FILES["package.json"])
        except (json.JSONDecodeError, KeyError):
            return
        required_deps = starter.get("dependencies") or {}
        required_dev = starter.get("devDependencies") or {}
        if not required_deps and not required_dev:
            return

        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        changed = False

        def _merge(section: str, required: dict[str, str]) -> None:
            nonlocal changed
            current = data.get(section)
            if not isinstance(current, dict):
                current = {}
                data[section] = current
            for name, version in required.items():
                if name not in current:
                    current[name] = version
                    changed = True

        _merge("dependencies", required_deps)
        _merge("devDependencies", required_dev)

        if changed:
            self.write_file(slug, "package.json", json.dumps(data, indent=2) + "\n")

    def list_projects(self) -> list[ProjectRecord]:
        if not self.root.exists():
            return []
        records: list[ProjectRecord] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            if not SLUG_RE.fullmatch(child.name):
                continue
            rec = self._try_read_project_json(child.name)
            if rec is not None:
                records.append(rec)
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records

    def get_project(self, slug: str) -> ProjectRecord | None:
        self._validate_slug(slug)
        return self._try_read_project_json(slug)

    def delete_project(self, slug: str) -> bool:
        self._validate_slug(slug)
        target = self.project_dir(slug).resolve()
        if not target.exists():
            return False
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("refusing to delete path outside storage root") from exc
        shutil.rmtree(target)
        return True

    def read_tree(self, slug: str) -> dict[str, Any]:
        proj = self.project_dir(slug)
        if not proj.exists():
            raise FileNotFoundError(slug)

        def walk(dir_path: Path, is_root: bool) -> dict[str, Any]:
            tree: dict[str, Any] = {}
            for entry in sorted(dir_path.iterdir(), key=lambda p: p.name):
                name = entry.name
                if is_root and name in _IGNORED_TOP_LEVEL:
                    continue
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    tree[name] = {"directory": walk(entry, is_root=False)}
                elif entry.is_file():
                    try:
                        contents = entry.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    tree[name] = {"file": {"contents": contents}}
            return tree

        return walk(proj, is_root=True)

    def write_file(self, slug: str, rel_path: str, content: str) -> Path:
        proj = self.project_dir(slug)
        if not proj.exists():
            raise FileNotFoundError(slug)
        target = safe_join(proj, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            target.write_text(content, encoding="utf-8")
        self._touch_project(slug)
        return target

    def delete_file(self, slug: str, rel_path: str) -> bool:
        proj = self.project_dir(slug)
        if not proj.exists():
            return False
        target = safe_join(proj, rel_path)
        if not target.exists():
            return False
        with self._write_lock:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        self._touch_project(slug)
        return True

    def append_prompt(
        self,
        slug: str,
        role: PromptRole,
        content: str,
        *,
        snapshot_id: str | None = None,
    ) -> PromptRecord:
        sidecar = self.sidecar_dir(slug)
        sidecar.mkdir(parents=True, exist_ok=True)
        record = PromptRecord(
            id=uuid.uuid4().hex,
            role=role,
            content=content,
            created_at=_now(),
            snapshot_id=snapshot_id,
        )
        payload = _prompt_adapter.dump_json(record).decode("utf-8")
        path = sidecar / PROMPTS_FILE
        with self._write_lock, open(path, "a", encoding="utf-8") as fp:
            fp.write(payload + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        self._touch_project(slug)
        return record

    def read_prompts(self, slug: str) -> list[PromptRecord]:
        path = self.sidecar_dir(slug) / PROMPTS_FILE
        if not path.exists():
            return []
        records: list[PromptRecord] = []
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(_prompt_adapter.validate_json(line))
                except Exception:  # noqa: BLE001
                    continue
        return records

    def pop_last_assistant_prompt(self, slug: str) -> PromptRecord | None:
        path = self.sidecar_dir(slug) / PROMPTS_FILE
        if not path.exists():
            return None

        with self._write_lock:
            raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            drop_idx: int | None = None
            dropped: PromptRecord | None = None
            for i in range(len(raw_lines) - 1, -1, -1):
                line = raw_lines[i].strip()
                if not line:
                    continue
                try:
                    rec = _prompt_adapter.validate_json(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.role == "assistant":
                    drop_idx = i
                    dropped = rec
                break

            if drop_idx is None or dropped is None:
                return None

            remaining = raw_lines[:drop_idx] + raw_lines[drop_idx + 1 :]
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fp:
                fp.writelines(remaining)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, path)
        self._touch_project(slug)
        return dropped

    def _snapshots_dir(self, slug: str) -> Path:
        return self.sidecar_dir(slug) / SNAPSHOTS_DIR

    def _snapshot_dir(self, slug: str, snapshot_id: str) -> Path:
        if not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            raise ValueError(f"invalid snapshot id: {snapshot_id!r}")
        return self._snapshots_dir(slug) / snapshot_id

    @staticmethod
    def _new_snapshot_id(now: datetime) -> str:
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{uuid.uuid4().hex[:4]}"

    def create_snapshot(self, slug: str, *, user_prompt: str = "") -> SnapshotRecord:
        proj = self.project_dir(slug)
        if not proj.exists():
            raise FileNotFoundError(slug)

        created_at = _now()
        for _ in range(8):
            snapshot_id = self._new_snapshot_id(created_at)
            dest = self._snapshot_dir(slug, snapshot_id)
            if not dest.exists():
                break
        else:
            raise RuntimeError("failed to allocate unique snapshot id")

        record = SnapshotRecord(
            id=snapshot_id,
            created_at=created_at,
            user_prompt=user_prompt[:4000],
            kind="pre-turn",
        )

        files_dir = dest / SNAPSHOT_FILES_DIR
        with self._write_lock:
            dest.mkdir(parents=True, exist_ok=False)
            files_dir.mkdir(parents=True, exist_ok=False)
            for entry in proj.iterdir():
                if entry.name in _IGNORED_TOP_LEVEL:
                    continue
                if entry.is_symlink():
                    continue
                target = files_dir / entry.name
                if entry.is_dir():
                    shutil.copytree(
                        entry,
                        target,
                        symlinks=False,
                        ignore=shutil.ignore_patterns(*_IGNORED_TOP_LEVEL),
                    )
                elif entry.is_file():
                    shutil.copy2(entry, target)

            meta_path = dest / SNAPSHOT_META_FILE
            payload = _snapshot_adapter.dump_json(record, indent=2).decode("utf-8")
            with open(meta_path, "w", encoding="utf-8") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())

        self._prune_snapshots(slug)
        return record

    def list_snapshots(self, slug: str) -> list[SnapshotRecord]:
        root = self._snapshots_dir(slug)
        if not root.exists():
            return []
        records: list[SnapshotRecord] = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if not SNAPSHOT_ID_RE.fullmatch(child.name):
                continue
            meta = child / SNAPSHOT_META_FILE
            if not meta.is_file():
                continue
            try:
                records.append(
                    _snapshot_adapter.validate_json(meta.read_text(encoding="utf-8"))
                )
            except Exception:  # noqa: BLE001
                continue
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def restore_snapshot(self, slug: str, snapshot_id: str) -> bool:
        proj = self.project_dir(slug)
        if not proj.exists():
            raise FileNotFoundError(slug)
        snap_dir = self._snapshot_dir(slug, snapshot_id)
        files_dir = snap_dir / SNAPSHOT_FILES_DIR
        if not snap_dir.is_dir() or not files_dir.is_dir():
            return False

        with self._write_lock:
            for entry in list(proj.iterdir()):
                if entry.name in _IGNORED_TOP_LEVEL:
                    continue
                if entry.is_symlink():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
                elif entry.is_file():
                    entry.unlink()

            for entry in files_dir.iterdir():
                if entry.name in _IGNORED_TOP_LEVEL:
                    continue
                target = proj / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, target, symlinks=False)
                elif entry.is_file():
                    shutil.copy2(entry, target)
        self._touch_project(slug)
        return True

    def delete_snapshot(self, slug: str, snapshot_id: str) -> bool:
        snap_dir = self._snapshot_dir(slug, snapshot_id)
        if not snap_dir.exists():
            return False
        target = snap_dir.resolve()
        try:
            target.relative_to(self._snapshots_dir(slug).resolve())
        except ValueError as exc:
            raise ValueError("refusing to delete path outside snapshots root") from exc
        with self._write_lock:
            shutil.rmtree(target)
        return True

    def _prune_snapshots(self, slug: str) -> None:
        records = self.list_snapshots(slug)
        if len(records) <= SNAPSHOT_KEEP:
            return
        for rec in records[SNAPSHOT_KEEP:]:
            try:
                self.delete_snapshot(slug, rec.id)
            except Exception:  # noqa: BLE001
                continue

    def _project_json_path(self, slug: str) -> Path:
        return self.sidecar_dir(slug) / PROJECT_FILE

    def _write_project_json(self, slug: str, record: ProjectRecord) -> None:
        path = self._project_json_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _project_adapter.dump_json(record, indent=2).decode("utf-8")
        with self._write_lock, open(path, "w", encoding="utf-8") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())

    def _try_read_project_json(self, slug: str) -> ProjectRecord | None:
        path = self._project_json_path(slug)
        if not path.exists():
            return None
        try:
            return _project_adapter.validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _touch_project(self, slug: str) -> None:
        rec = self._try_read_project_json(slug)
        if rec is None:
            return
        updated = rec.model_copy(update={"updated_at": _now()})
        self._write_project_json(slug, updated)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    from .config import CoreConfig
    return Storage(CoreConfig().opener_apps_dir)


def reset_storage_cache() -> None:
    get_storage.cache_clear()


def iter_ignored_top_level() -> Iterable[str]:
    return iter(_IGNORED_TOP_LEVEL)
