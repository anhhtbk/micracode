"""Unit tests for the local-filesystem storage module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from micracode_core.storage import SLUG_RE, Storage, safe_join, slugify


class TestSlugify:
    def test_happy_path(self) -> None:
        assert slugify("My Cool App") == "my-cool-app"

    def test_collapses_punctuation(self) -> None:
        assert slugify("Hello,   World!!!") == "hello-world"

    def test_trims_leading_and_trailing(self) -> None:
        assert slugify("--hi--") == "hi"

    def test_rejects_empty(self) -> None:
        assert slugify("") == ""
        assert slugify("   ") == ""
        assert slugify("!!!") == ""

    def test_truncates_to_63(self) -> None:
        long = slugify("x" * 200)
        assert len(long) == 63
        assert SLUG_RE.fullmatch(long)


class TestUniqueSlug:
    def test_returns_base_when_free(self, storage: Storage) -> None:
        assert storage.unique_slug("todo") == "todo"

    def test_suffixes_on_collision(self, storage: Storage) -> None:
        (storage.root / "todo").mkdir(parents=True)
        assert storage.unique_slug("todo") == "todo-2"
        (storage.root / "todo-2").mkdir(parents=True)
        assert storage.unique_slug("todo") == "todo-3"

    def test_fallback_for_unusable_name(self, storage: Storage) -> None:
        slug = storage.unique_slug("!!!")
        assert slug.startswith("project-")
        assert SLUG_RE.fullmatch(slug)


class TestSafeJoin:
    def test_simple(self, tmp_path: Path) -> None:
        target = safe_join(tmp_path, "a/b/c.txt")
        assert target == (tmp_path / "a/b/c.txt").resolve()

    def test_rejects_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            safe_join(tmp_path, "/etc/passwd")

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            safe_join(tmp_path, "../escape")

    def test_rejects_deep_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            safe_join(tmp_path, "a/b/../../../etc")

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlinks not supported on this platform",
    )
    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        import ctypes
        is_admin = False
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()  # type: ignore[attr-defined]
        except Exception:
            pass
        if not is_admin:
            pytest.skip("symlink creation requires admin rights on Windows")

        outside = tmp_path.parent / "outside-target"
        outside.mkdir(exist_ok=True)
        try:
            link = tmp_path / "evil"
            link.symlink_to(outside)
            with pytest.raises(ValueError):
                safe_join(tmp_path, "evil/secret.txt")
        finally:
            if outside.exists():
                outside.rmdir()


class TestProjectCrud:
    def test_create_and_list(self, storage: Storage) -> None:
        a = storage.create_project("Todo App")
        b = storage.create_project("Todo App")  # collision -> slug-2
        assert a.id == "todo-app"
        assert b.id == "todo-app-2"

        records = storage.list_projects()
        assert [r.id for r in records] == ["todo-app-2", "todo-app"]

    def test_get_unknown(self, storage: Storage) -> None:
        assert storage.get_project("missing") is None

    def test_delete(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        assert storage.delete_project(rec.id) is True
        assert storage.get_project(rec.id) is None
        assert storage.delete_project(rec.id) is False

    def test_invalid_slug_rejected(self, storage: Storage) -> None:
        with pytest.raises(ValueError):
            storage.get_project("../etc")
        with pytest.raises(ValueError):
            storage.project_dir("with spaces")


class TestReadTree:
    def test_skips_ignored_dirs(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        proj = storage.project_dir(rec.id)
        (proj / "node_modules").mkdir()
        (proj / "node_modules" / "pkg.txt").write_text("noise")
        (proj / "app").mkdir(exist_ok=True)
        (proj / "app" / "page.tsx").write_text("export default () => null;")
        (proj / "package.json").write_text("{}\n")

        tree = storage.read_tree(rec.id)
        assert "node_modules" not in tree
        assert ".micracode" not in tree
        assert tree["app"]["directory"]["page.tsx"]["file"]["contents"].startswith(
            "export default"
        )
        assert tree["package.json"]["file"]["contents"] == "{}\n"


class TestFileWrites:
    def test_write_and_delete(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        storage.write_file(rec.id, "app/page.tsx", "hi")
        proj = storage.project_dir(rec.id)
        assert (proj / "app" / "page.tsx").read_text() == "hi"

        assert storage.delete_file(rec.id, "app/page.tsx") is True
        assert not (proj / "app" / "page.tsx").exists()

    def test_write_rejects_traversal(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        with pytest.raises(ValueError):
            storage.write_file(rec.id, "../evil.txt", "x")


class TestPrompts:
    def test_append_and_read(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        storage.append_prompt(rec.id, "user", "hello")
        storage.append_prompt(rec.id, "assistant", "world")
        prompts = storage.read_prompts(rec.id)
        assert [p.role for p in prompts] == ["user", "assistant"]
        assert [p.content for p in prompts] == ["hello", "world"]

    def test_skips_corrupt_rows(self, storage: Storage) -> None:
        rec = storage.create_project("Thing")
        path = storage.sidecar_dir(rec.id) / "prompts.jsonl"
        storage.append_prompt(rec.id, "user", "ok")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write("not-json\n")
        storage.append_prompt(rec.id, "assistant", "reply")

        prompts = storage.read_prompts(rec.id)
        assert [p.content for p in prompts] == ["ok", "reply"]
