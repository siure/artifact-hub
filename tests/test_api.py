from __future__ import annotations

import time

from conftest import publish, publish_plan


def test_health_and_no_index(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").status_code == 404
    assert client.get("/v1/artifacts").status_code == 404


def test_publish_requires_authentication(client):
    response = client.post("/v1/artifacts/x.txt", content=b"hello")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_legacy_publish_removed_and_configuration_auth_check(client, auth_headers):
    assert client.post("/v1/artifacts?filename=x.txt", headers=auth_headers).status_code == 404
    assert client.get("/v1/auth/check", headers=auth_headers).status_code == 200
    assert client.get("/v1/auth/check").status_code == 401
    assert client.delete("/v1/artifacts/x.txt", headers=auth_headers).status_code == 405


def test_publish_and_retrieve(client, auth_headers):
    response = publish(client, auth_headers, b"hello artifact")
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/plain")
    url = response.text.strip()
    assert url.startswith("https://artifacts.example.test/a/")

    path = url.removeprefix("https://artifacts.example.test")
    download = client.get(path)
    assert download.status_code == 200
    assert download.content == b"hello artifact"
    assert download.headers["content-type"].startswith("text/plain")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["etag"].startswith('"sha256-')
    assert download.headers["content-disposition"].startswith("inline")

    head = client.head(path)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "14"

    cached = client.get(path, headers={"If-None-Match": download.headers["etag"]})
    assert cached.status_code == 304


def test_ranges(client, auth_headers):
    url = publish(client, auth_headers, b"0123456789").text.strip()
    path = url.removeprefix("https://artifacts.example.test")

    response = client.get(path, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"

    suffix = client.get(path, headers={"Range": "bytes=-3"})
    assert suffix.status_code == 206
    assert suffix.content == b"789"

    invalid = client.get(path, headers={"Range": "bytes=100-200"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */10"

    if_range_miss = client.get(
        path, headers={"Range": "bytes=2-5", "If-Range": '"different-etag"'}
    )
    assert if_range_miss.status_code == 200
    assert if_range_miss.content == b"0123456789"

    head = client.head(path, headers={"Range": "bytes=2-5"})
    assert head.status_code == 200
    assert head.headers["content-length"] == "10"
    assert "content-range" not in head.headers


def test_size_and_quota_limits(client, auth_headers):
    too_large = publish(client, auth_headers, b"x" * 1025)
    assert too_large.status_code == 413

    for number in range(4):
        response = publish(
            client,
            auth_headers,
            b"x" * 1024,
            filename=f"{number}.bin",
            media_type="application/octet-stream",
        )
        assert response.status_code == 201

    full = publish(client, auth_headers, b"x", filename="overflow.bin")
    assert full.status_code == 507


def test_invalid_filenames(client, auth_headers):
    assert publish(client, auth_headers, b"x", filename="..").status_code == 404
    assert publish(client, auth_headers, b"x", filename="bad\nname.txt").status_code == 400


def test_same_filename_creates_distinct_immutable_artifacts(client, auth_headers):
    first = publish(client, auth_headers, b"first", filename="same.txt").text.strip()
    second = publish(client, auth_headers, b"second", filename="same.txt").text.strip()
    assert first != second
    assert client.get(first.removeprefix("https://artifacts.example.test")).content == b"first"
    assert client.get(second.removeprefix("https://artifacts.example.test")).content == b"second"


def test_named_plan_has_stable_url_and_immutable_versions(client, auth_headers):
    first = publish_plan(client, auth_headers, b"<h1>first</h1>")
    assert first.status_code == 201
    assert first.text.strip() == "https://artifacts.example.test/p/project-plan.html"
    plan_path = "/p/project-plan.html"
    first_download = client.get(plan_path)
    assert first_download.content == b"<h1>first</h1>"
    assert first_download.headers["content-type"].startswith("text/html")
    assert "worker-src" not in first_download.headers["content-security-policy"]

    database = client.app.state.database
    first_artifact = database.get_plan("project-plan.html")
    assert first_artifact is not None

    second = publish_plan(client, auth_headers, b"<h1>second</h1>")
    assert second.text.strip() == first.text.strip()
    assert client.get(plan_path).content == b"<h1>second</h1>"

    second_artifact = database.get_plan("project-plan.html")
    assert second_artifact is not None
    assert second_artifact.id != first_artifact.id
    old_path = f"/a/{first_artifact.id}/{first_artifact.slug}"
    assert client.get(old_path).content == b"<h1>first</h1>"


def test_named_plan_requires_authentication_and_html_name(client, auth_headers):
    unauthorized = client.put("/v1/plans/plan.html", content=b"plan")
    assert unauthorized.status_code == 401
    invalid = client.put("/v1/plans/plan.txt", content=b"plan", headers=auth_headers)
    assert invalid.status_code == 400
    assert client.get("/p/missing.html").status_code == 404


def test_expired_artifact_is_hidden(client, auth_headers, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr("artifact_hub.storage.time.time", lambda: now)
    url = publish(client, auth_headers, b"short").text.strip()
    path = url.removeprefix("https://artifacts.example.test")
    monkeypatch.setattr("artifact_hub.app.time.time", lambda: now + 3601)
    assert client.get(path).status_code == 404
