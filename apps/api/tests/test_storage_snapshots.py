"""Unit tests for per-project snapshots (create/list/restore/prune)."""

from __future__ import annotations

import pytest

from micracode_core import storage as storage_module
from micracode_core.storage import (
    SNAPSHOT_FILES_DIR,
    SNAPSHOT_ID_RE,
    Storage,
)


class TestCreateSnapshot:
    def test_captures_project_tree(self, storage: Storage) -> None:
        rec = storage.create_project("Snap One")
        storage.write_file(rec.id, "app/page.tsx", "export default () => null;\n")
        storage.write_file(rec.id, "package.json", '{"name":"x"}\n')

        snap = storage.create_snapshot(rec.id, user_prompt="make it nicer")

        assert SNAPSHOT_ID_RE.fullmatch(snap.id)
        assert snap.user_prompt == "make it nicer"
        assert snap.kind == "pre-turn"

        snap_files = (
            storage.sidecar_dir(rec.id)
            / "snapshots"
            / snap.id
            / SNAPSHOT_FILES_DIR
        )
        assert (snap_files / "app" / "page.tsx").read_text() == (
            "export default () => null;\n"
        )
        assert (snap_files / "package.json").read_text() == '{"name":"x"}\n'

    def test_excludes_ignored_top_level(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Two")
        proj = storage.project_dir(rec.id)
        (proj / "node_modules").mkdir(parents=True, exist_ok=True)
        (proj / "node_modules" / "pkg.js").write_text("noise")
        (proj / ".next").mkdir(parents=True, exist_ok=True)
        (proj / ".next" / "build.log").write_text("build output")

        snap = storage.create_snapshot(rec.id)

        snap_files = (
            storage.sidecar_dir(rec.id)
            / "snapshots"
            / snap.id
            / SNAPSHOT_FILES_DIR
        )
        assert not (snap_files / "node_modules").exists()
        assert not (snap_files / ".next").exists()
        assert not (snap_files / ".micracode").exists()
        assert (snap_files / "package.json").is_file()

    def test_truncates_long_user_prompt(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Trunc")
        prompt = "x" * 10_000
        snap = storage.create_snapshot(rec.id, user_prompt=prompt)
        assert len(snap.user_prompt) == 4000


class TestListSnapshots:
    def test_orders_newest_first(self, storage: Storage) -> None:
        rec = storage.create_project("Snap List")
        first = storage.create_snapshot(rec.id, user_prompt="one")
        second = storage.create_snapshot(rec.id, user_prompt="two")
        records = storage.list_snapshots(rec.id)
        assert records[0].id == second.id
        assert records[-1].id == first.id

    def test_empty_when_no_snapshots(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Empty")
        assert storage.list_snapshots(rec.id) == []

    def test_skips_corrupt_metadata(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Corrupt")
        snap = storage.create_snapshot(rec.id)
        meta = (
            storage.sidecar_dir(rec.id) / "snapshots" / snap.id / "project.json"
        )
        meta.write_text("not json")
        assert storage.list_snapshots(rec.id) == []


class TestRestoreSnapshot:
    def test_round_trip(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Round")
        storage.write_file(rec.id, "app/page.tsx", "original\n")
        snap = storage.create_snapshot(rec.id)

        storage.write_file(rec.id, "app/page.tsx", "mutated\n")
        storage.write_file(rec.id, "app/new.tsx", "added\n")
        storage.delete_file(rec.id, "package.json")

        restored = storage.restore_snapshot(rec.id, snap.id)
        assert restored is True

        proj = storage.project_dir(rec.id)
        assert (proj / "app" / "page.tsx").read_text() == "original\n"
        assert not (proj / "app" / "new.tsx").exists()
        assert (proj / "package.json").is_file()

    def test_preserves_ignored_top_level(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Preserve")
        snap = storage.create_snapshot(rec.id)

        proj = storage.project_dir(rec.id)
        (proj / "node_modules").mkdir(parents=True, exist_ok=True)
        (proj / "node_modules" / "keep.js").write_text("cached dep")

        storage.restore_snapshot(rec.id, snap.id)
        assert (proj / "node_modules" / "keep.js").read_text() == "cached dep"

    def test_missing_snapshot_returns_false(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Missing")
        assert (
            storage.restore_snapshot(rec.id, "99990101T000000Z-dead") is False
        )

    def test_invalid_snapshot_id_raises(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Bad")
        with pytest.raises(ValueError):
            storage.restore_snapshot(rec.id, "../evil")


class TestDeleteSnapshot:
    def test_delete_removes_files(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Del")
        snap = storage.create_snapshot(rec.id)
        snap_dir = storage.sidecar_dir(rec.id) / "snapshots" / snap.id
        assert snap_dir.exists()
        assert storage.delete_snapshot(rec.id, snap.id) is True
        assert not snap_dir.exists()

    def test_delete_unknown_returns_false(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Del Unknown")
        assert (
            storage.delete_snapshot(rec.id, "99990101T000000Z-beef") is False
        )


class TestPruneSnapshots:
    def test_respects_keep_cap(
        self, storage: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_module, "SNAPSHOT_KEEP", 3)
        rec = storage.create_project("Snap Prune")
        ids: list[str] = []
        for _ in range(6):
            ids.append(storage.create_snapshot(rec.id).id)

        records = storage.list_snapshots(rec.id)
        # Only the three newest must remain.
        assert len(records) == 3
        remaining = {r.id for r in records}
        assert remaining == set(ids[-3:])


class TestPopLastAssistantPrompt:
    def test_drops_only_last_assistant(self, storage: Storage) -> None:
        rec = storage.create_project("Pop")
        storage.append_prompt(rec.id, "user", "u1")
        storage.append_prompt(rec.id, "assistant", "a1")
        storage.append_prompt(rec.id, "user", "u2")
        storage.append_prompt(rec.id, "assistant", "a2")

        dropped = storage.pop_last_assistant_prompt(rec.id)
        assert dropped is not None
        assert dropped.content == "a2"
        remaining = storage.read_prompts(rec.id)
        assert [p.content for p in remaining] == ["u1", "a1", "u2"]

    def test_no_assistant_rows_is_noop(self, storage: Storage) -> None:
        rec = storage.create_project("Pop Empty")
        storage.append_prompt(rec.id, "user", "u1")
        dropped = storage.pop_last_assistant_prompt(rec.id)
        assert dropped is None
        assert [p.content for p in storage.read_prompts(rec.id)] == ["u1"]

    def test_ends_on_user_row_is_noop(self, storage: Storage) -> None:
        rec = storage.create_project("Pop Trailing")
        storage.append_prompt(rec.id, "user", "u1")
        storage.append_prompt(rec.id, "assistant", "a1")
        storage.append_prompt(rec.id, "user", "u2")
        dropped = storage.pop_last_assistant_prompt(rec.id)
        # Most recent non-assistant row is a user, so nothing is popped.
        assert dropped is None
        assert [p.content for p in storage.read_prompts(rec.id)] == [
            "u1",
            "a1",
            "u2",
        ]


class TestAppendPromptSnapshotId:
    def test_persists_snapshot_id(self, storage: Storage) -> None:
        rec = storage.create_project("Snap Append")
        snap = storage.create_snapshot(rec.id)
        storage.append_prompt(
            rec.id, "assistant", "reply", snapshot_id=snap.id
        )
        [p] = storage.read_prompts(rec.id)
        assert p.snapshot_id == snap.id

    def test_backward_compatible_without_field(
        self, storage: Storage
    ) -> None:
        rec = storage.create_project("Snap Append Compat")
        # Hand-write a legacy row with no snapshot_id.
        path = storage.sidecar_dir(rec.id) / "prompts.jsonl"
        path.write_text(
            '{"id":"abc","role":"assistant","content":"hi",'
            '"created_at":"2024-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        [p] = storage.read_prompts(rec.id)
        assert p.snapshot_id is None
