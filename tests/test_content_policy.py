from __future__ import annotations

from conftest import publish


def artifact_path(response) -> str:
    return response.text.strip().removeprefix("https://artifacts.example.test")


def test_html_is_interactive_but_sandboxed(client, auth_headers):
    uploaded = publish(
        client,
        auth_headers,
        b"<button onclick=\"this.textContent='ok'\">run</button>",
        filename="plan.html",
        media_type="text/html",
    )
    response = client.get(artifact_path(uploaded))
    csp = response.headers["content-security-policy"]
    assert response.status_code == 200
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp
    assert "form-action 'none'" in csp
    assert "worker-src" not in csp
    assert response.headers["content-disposition"].startswith("inline")


def test_svg_disables_scripts(client, auth_headers):
    uploaded = publish(
        client,
        auth_headers,
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        filename="diagram.svg",
        media_type="image/svg+xml",
    )
    response = client.get(artifact_path(uploaded))
    csp = response.headers["content-security-policy"]
    assert "sandbox" in csp
    assert "allow-scripts" not in csp


def test_pdf_and_images_inline_unknown_files_download(client, auth_headers):
    pdf = publish(
        client,
        auth_headers,
        b"%PDF-1.4\n%%EOF",
        filename="report.pdf",
    )
    assert client.get(artifact_path(pdf)).headers["content-disposition"].startswith("inline")

    image = publish(
        client,
        auth_headers,
        b"\x89PNG\r\n\x1a\n",
        filename="image.png",
    )
    assert client.get(artifact_path(image)).headers["content-disposition"].startswith("inline")

    binary = publish(
        client,
        auth_headers,
        b"binary",
        filename="archive.zip",
        media_type="application/zip",
    )
    response = client.get(artifact_path(binary))
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_explicit_content_type_overrides_filename_inference(client, auth_headers):
    uploaded = publish(
        client,
        auth_headers,
        b"<h1>plan</h1>",
        filename="generated-file",
        media_type="text/html",
    )
    response = client.get(artifact_path(uploaded))
    assert response.headers["content-type"].startswith("text/html")
    assert "content-security-policy" in response.headers


def test_unicode_filename_is_encoded_safely(client, auth_headers):
    uploaded = publish(client, auth_headers, b"hello", filename="résumé 2026.txt")
    response = client.get(artifact_path(uploaded))
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9%202026.txt" in disposition
    assert "\n" not in disposition
