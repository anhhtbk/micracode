"""Smoke tests for the Next.js starter scaffold.

Locks in the high-value design toolkit (Tailwind + CSS tokens + motion +
icons + Inter) so a future refactor doesn't silently regress generated
sites back to unstyled HTML.
"""

from __future__ import annotations

import json

from micracode_core.starter.next_default import NEXT_STARTER_FILES
from micracode_core.storage import Storage


REQUIRED_PATHS = (
    "package.json",
    "tsconfig.json",
    "next.config.mjs",
    "postcss.config.mjs",
    "tailwind.config.ts",
    "app/globals.css",
    "app/layout.tsx",
    "app/page.tsx",
    "lib/utils.ts",
)


def test_starter_ships_design_toolkit_files() -> None:
    missing = [p for p in REQUIRED_PATHS if p not in NEXT_STARTER_FILES]
    assert not missing, f"starter missing required files: {missing}"


def test_package_json_declares_design_toolkit() -> None:
    pkg = json.loads(NEXT_STARTER_FILES["package.json"])
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name in (
        "tailwindcss",
        "postcss",
        "autoprefixer",
        "lucide-react",
        "framer-motion",
        "clsx",
        "tailwind-merge",
        "next",
        "react",
    ):
        assert name in deps, f"package.json missing dependency: {name}"


def test_globals_css_wires_tailwind_and_tokens() -> None:
    css = NEXT_STARTER_FILES["app/globals.css"]
    assert "@tailwind base" in css
    assert "@tailwind components" in css
    assert "@tailwind utilities" in css
    assert "--background" in css and "--foreground" in css
    assert ".dark" in css


def test_layout_loads_globals_and_inter_font() -> None:
    layout = NEXT_STARTER_FILES["app/layout.tsx"]
    assert 'import "./globals.css"' in layout
    assert "next/font/google" in layout
    assert "Inter" in layout
    assert "--font-sans" in layout


def test_tailwind_config_uses_css_variable_tokens() -> None:
    cfg = NEXT_STARTER_FILES["tailwind.config.ts"]
    assert 'darkMode: "class"' in cfg
    assert "hsl(var(--background))" in cfg
    assert "hsl(var(--primary))" in cfg
    assert "var(--font-sans)" in cfg


def test_utils_exports_cn_helper() -> None:
    utils = NEXT_STARTER_FILES["lib/utils.ts"]
    assert "export function cn" in utils
    assert "tailwind-merge" in utils
    assert "clsx" in utils


def test_next_config_allows_remote_images() -> None:
    cfg = NEXT_STARTER_FILES["next.config.mjs"]
    assert "remotePatterns" in cfg
    assert "images.unsplash.com" in cfg


def test_ensure_next_preview_layout_backfills_missing_starter_deps(
    storage: Storage,
) -> None:
    """Legacy projects whose ``package.json`` predates the toolkit upgrade must
    get Tailwind & friends merged in on the next load — otherwise ``npm install``
    in WebContainer won't have the packages Next's PostCSS pipeline loads."""
    storage.create_project("p-legacy")
    legacy_pkg = {
        "name": "app",
        "version": "0.0.1",
        "private": True,
        "scripts": {"dev": "next dev --hostname 0.0.0.0 --port 3000"},
        "dependencies": {
            "next": "14.2.18",
            "react": "18.3.1",
            "react-dom": "18.3.1",
        },
        "devDependencies": {
            "typescript": "5.5.4",
        },
    }
    storage.write_file(
        "p-legacy", "package.json", json.dumps(legacy_pkg, indent=2) + "\n"
    )

    storage.ensure_next_preview_layout("p-legacy")

    proj = storage.project_dir("p-legacy")
    merged = json.loads((proj / "package.json").read_text(encoding="utf-8"))
    deps = {**merged.get("dependencies", {}), **merged.get("devDependencies", {})}
    for name in (
        "tailwindcss",
        "postcss",
        "autoprefixer",
        "lucide-react",
        "framer-motion",
        "clsx",
        "tailwind-merge",
    ):
        assert name in deps, f"missing after backfill: {name}"

    assert merged["dependencies"]["next"] == "14.2.18"
    assert merged["devDependencies"]["typescript"] == "5.5.4"


def test_ensure_next_preview_layout_is_idempotent(storage: Storage) -> None:
    """Running the backfill twice should not keep rewriting ``package.json``."""
    storage.create_project("p-fresh")

    proj = storage.project_dir("p-fresh")
    first = (proj / "package.json").read_text(encoding="utf-8")

    storage.ensure_next_preview_layout("p-fresh")
    second = (proj / "package.json").read_text(encoding="utf-8")

    assert first == second
