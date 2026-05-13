"""End-to-end HTTP tests for `/v1/projects/...`."""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient


def test_list_empty(client: TestClient) -> None:
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    assert resp.json() == []


def test_new_project_includes_package_json_with_dev_script(client: TestClient) -> None:
    record = client.post("/v1/projects", json={"name": "Starter"}).json()
    resp = client.get(f"/v1/projects/{record['id']}/files")
    assert resp.status_code == 200
    tree = resp.json()["tree"]
    assert "package.json" in tree
    raw = tree["package.json"]["file"]["contents"]
    assert '"dev"' in raw
    assert "next dev" in raw


def test_get_files_restores_missing_package_json(
    client: TestClient, opener_apps_dir
) -> None:
    record = client.post("/v1/projects", json={"name": "Repair"}).json()
    pid = record["id"]
    pkg = opener_apps_dir / pid / "package.json"
    assert pkg.is_file()
    pkg.unlink()

    resp = client.get(f"/v1/projects/{pid}/files")
    assert resp.status_code == 200
    assert pkg.is_file()
    tree = resp.json()["tree"]
    data = json.loads(tree["package.json"]["file"]["contents"])
    assert isinstance(data["scripts"].get("dev"), str)
    assert "next dev" in data["scripts"]["dev"]


def test_get_files_adds_dev_script_when_missing(
    client: TestClient,
) -> None:
    record = client.post("/v1/projects", json={"name": "No Dev"}).json()
    pid = record["id"]
    client.put(
        f"/v1/projects/{pid}/files",
        json={"path": "package.json", "content": '{"name": "x", "private": true}\n'},
    )

    tree = client.get(f"/v1/projects/{pid}/files").json()["tree"]
    data = json.loads(tree["package.json"]["file"]["contents"])
    assert "next dev" in data["scripts"]["dev"]


def test_create_get_list_delete(client: TestClient) -> None:
    create = client.post("/v1/projects", json={"name": "Todo App"})
    assert create.status_code == 201
    record = create.json()
    assert record["id"] == "todo-app"
    assert record["name"] == "Todo App"
    assert record["template"] == "next"

    got = client.get(f"/v1/projects/{record['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == "todo-app"

    listed = client.get("/v1/projects").json()
    assert [r["id"] for r in listed] == ["todo-app"]

    deleted = client.delete(f"/v1/projects/{record['id']}")
    assert deleted.status_code == 204

    missing = client.get(f"/v1/projects/{record['id']}")
    assert missing.status_code == 404


def test_get_project_404(client: TestClient) -> None:
    assert client.get("/v1/projects/missing").status_code == 404


def test_slug_regex_rejects_bad_ids(client: TestClient) -> None:
    # FastAPI path validation rejects these before they reach the handler.
    assert client.get("/v1/projects/Bad_Slug").status_code == 422
    assert client.get("/v1/projects/with space").status_code == 422
    assert client.delete("/v1/projects/UPPER").status_code == 422


def test_files_endpoint_returns_tree(client: TestClient, opener_apps_dir) -> None:
    record = client.post("/v1/projects", json={"name": "Demo"}).json()
    proj_dir = opener_apps_dir / record["id"]
    (proj_dir / "app").mkdir(parents=True, exist_ok=True)
    (proj_dir / "app" / "page.tsx").write_text("export default () => null;\n")

    resp = client.get(f"/v1/projects/{record['id']}/files")
    assert resp.status_code == 200
    tree = resp.json()["tree"]
    assert "app" in tree
    assert (
        tree["app"]["directory"]["page.tsx"]["file"]["contents"]
        == "export default () => null;\n"
    )


def test_put_file_persists_and_updates_tree(
    client: TestClient, opener_apps_dir
) -> None:
    record = client.post("/v1/projects", json={"name": "Edit Me"}).json()
    pid = record["id"]
    proj_dir = opener_apps_dir / pid
    (proj_dir / "app").mkdir(parents=True, exist_ok=True)
    (proj_dir / "app" / "page.tsx").write_text("export default () => null;\n")

    put = client.put(
        f"/v1/projects/{pid}/files",
        json={"path": "app/page.tsx", "content": "export default () => <p>ok</p>;\n"},
    )
    assert put.status_code == 204
    assert (proj_dir / "app" / "page.tsx").read_text() == "export default () => <p>ok</p>;\n"

    tree = client.get(f"/v1/projects/{pid}/files").json()["tree"]
    assert tree["app"]["directory"]["page.tsx"]["file"]["contents"] == (
        "export default () => <p>ok</p>;\n"
    )


def test_put_file_rejects_sidecar_path(client: TestClient) -> None:
    record = client.post("/v1/projects", json={"name": "Side"}).json()
    pid = record["id"]
    resp = client.put(
        f"/v1/projects/{pid}/files",
        json={"path": ".micracode/project.json", "content": "{}"},
    )
    assert resp.status_code == 400


def test_put_file_404_unknown_project(client: TestClient) -> None:
    resp = client.put(
        "/v1/projects/missing-project/files",
        json={"path": "x.txt", "content": "a"},
    )
    assert resp.status_code == 404


def test_download_zip_returns_archive(client: TestClient, opener_apps_dir) -> None:
    record = client.post("/v1/projects", json={"name": "Zip Me"}).json()
    pid = record["id"]
    proj_dir = opener_apps_dir / pid
    (proj_dir / "app").mkdir(parents=True, exist_ok=True)
    (proj_dir / "app" / "page.tsx").write_text("export default () => null;\n")
    (proj_dir / "node_modules").mkdir(parents=True, exist_ok=True)
    (proj_dir / "node_modules" / "junk.js").write_text("nope")

    resp = client.get(f"/v1/projects/{pid}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert f'filename="{pid}.zip"' in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())

    assert f"{pid}/package.json" in names
    assert f"{pid}/app/page.tsx" in names
    assert not any(n.startswith(f"{pid}/.micracode/") for n in names)
    assert not any(n.startswith(f"{pid}/node_modules/") for n in names)


def test_download_zip_404_unknown(client: TestClient) -> None:
    assert client.get("/v1/projects/missing-project/download").status_code == 404


def test_prompts_endpoint_returns_history(
    client: TestClient, opener_apps_dir
) -> None:
    record = client.post("/v1/projects", json={"name": "Demo"}).json()
    # Seed a prompt via the storage module so we don't need the SSE stream here.
    from micracode_core.storage import Storage

    storage = Storage(opener_apps_dir)
    storage.append_prompt(record["id"], "user", "hi there")

    resp = client.get(f"/v1/projects/{record['id']}/prompts")
    assert resp.status_code == 200
    prompts = resp.json()
    assert len(prompts) == 1
    assert prompts[0]["role"] == "user"
    assert prompts[0]["content"] == "hi there"


def test_pop_last_assistant_prompt_endpoint(
    client: TestClient, opener_apps_dir
) -> None:
    from micracode_core.storage import Storage

    record = client.post("/v1/projects", json={"name": "Pop"}).json()
    pid = record["id"]
    storage = Storage(opener_apps_dir)
    storage.append_prompt(pid, "user", "u1")
    storage.append_prompt(pid, "assistant", "a1")

    resp = client.post(f"/v1/projects/{pid}/prompts/pop-assistant")
    assert resp.status_code == 200
    assert resp.json() == {"popped": True}

    history = client.get(f"/v1/projects/{pid}/prompts").json()
    assert [p["content"] for p in history] == ["u1"]

    # Second pop with no remaining assistant rows is a no-op.
    again = client.post(f"/v1/projects/{pid}/prompts/pop-assistant")
    assert again.status_code == 200
    assert again.json() == {"popped": False}


def test_pop_last_assistant_prompt_404_unknown(client: TestClient) -> None:
    resp = client.post("/v1/projects/missing-project/prompts/pop-assistant")
    assert resp.status_code == 404


def test_list_snapshots_empty(client: TestClient) -> None:
    record = client.post("/v1/projects", json={"name": "No Snaps"}).json()
    resp = client.get(f"/v1/projects/{record['id']}/snapshots")
    assert resp.status_code == 200
    assert resp.json() == []


def test_snapshot_restore_round_trip(
    client: TestClient, opener_apps_dir
) -> None:
    from micracode_core.storage import Storage

    record = client.post("/v1/projects", json={"name": "Round"}).json()
    pid = record["id"]
    proj = opener_apps_dir / pid

    # Seed content and take a snapshot via storage directly (the SSE
    # stream is exercised separately in test_generate_cancellation.py).
    storage = Storage(opener_apps_dir)
    storage.write_file(pid, "app/page.tsx", "original\n")
    snap = storage.create_snapshot(pid, user_prompt="pre-turn")

    listed = client.get(f"/v1/projects/{pid}/snapshots").json()
    assert len(listed) == 1
    assert listed[0]["id"] == snap.id
    assert listed[0]["user_prompt"] == "pre-turn"

    # Mutate the project, then restore.
    (proj / "app" / "page.tsx").write_text("mutated\n")

    restore = client.post(
        f"/v1/projects/{pid}/snapshots/{snap.id}/restore"
    )
    assert restore.status_code == 204
    assert (proj / "app" / "page.tsx").read_text() == "original\n"


def test_snapshot_restore_404_unknown_snapshot(
    client: TestClient,
) -> None:
    record = client.post("/v1/projects", json={"name": "Missing Snap"}).json()
    resp = client.post(
        f"/v1/projects/{record['id']}/snapshots/99990101T000000Z-dead/restore"
    )
    assert resp.status_code == 404


def test_snapshot_restore_rejects_traversal(client: TestClient) -> None:
    record = client.post("/v1/projects", json={"name": "Traverse"}).json()
    # FastAPI path validation rejects ids that don't match the pattern.
    resp = client.post(
        f"/v1/projects/{record['id']}/snapshots/..%2Fevil/restore"
    )
    assert resp.status_code in (400, 404, 422)


def test_snapshot_delete(client: TestClient, opener_apps_dir) -> None:
    from micracode_core.storage import Storage

    record = client.post("/v1/projects", json={"name": "Del Snap"}).json()
    pid = record["id"]
    storage = Storage(opener_apps_dir)
    snap = storage.create_snapshot(pid)

    resp = client.delete(f"/v1/projects/{pid}/snapshots/{snap.id}")
    assert resp.status_code == 204
    assert storage.list_snapshots(pid) == []


def test_snapshot_endpoints_404_unknown_project(client: TestClient) -> None:
    assert (
        client.get("/v1/projects/missing/snapshots").status_code == 404
    )
    assert (
        client.post(
            "/v1/projects/missing/snapshots/20260101T000000Z-abcd/restore"
        ).status_code
        == 404
    )
    assert (
        client.delete(
            "/v1/projects/missing/snapshots/20260101T000000Z-abcd"
        ).status_code
        == 404
    )
