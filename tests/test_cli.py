import argparse
import io
import tomllib

import pytest

from artifact_hub import cli
from conftest import TOKEN


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    monkeypatch.setenv("ARTIFACT_HUB_CONFIG_DIR", str(directory))
    return directory


def test_configure_hidden_prompt_and_relative_token(publisher, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: TOKEN)
    monkeypatch.setattr(cli, "request", lambda *args: calls.append(args))
    cli.configure(argparse.Namespace(endpoint="https://hub.example/", token_file=None))
    config = tomllib.loads((publisher / "client.toml").read_text())
    assert config == {"endpoint": "https://hub.example", "token_file": "publish.token"}
    assert calls == [("https://hub.example", TOKEN, "GET", "/v1/auth/check")]
    assert publisher.stat().st_mode & 0o777 == 0o700
    for filename in ("publish.token", "client.toml"):
        assert (publisher / filename).stat().st_mode & 0o777 == 0o600
    assert TOKEN not in capsys.readouterr().out


def test_failed_setup_preserves_existing_config(publisher, tmp_path, monkeypatch):
    publisher.mkdir()
    config = publisher / "client.toml"
    config.write_text("existing config")
    token = tmp_path / "existing.token"
    token.write_text(TOKEN)
    def reject(*args):
        raise cli.ClientError("Authentication failed")
    monkeypatch.setattr(cli, "request", reject)
    with pytest.raises(cli.ClientError):
        cli.configure(argparse.Namespace(endpoint="https://hub.example", token_file=str(token)))
    assert config.read_text() == "existing config"
    assert token.read_text() == TOKEN


@pytest.mark.parametrize("command,name,method,route", [
    ("push", None, "POST", "/v1/artifacts/r%C3%A9sum%C3%A9%20file.html"),
    ("plan", "project.html", "PUT", "/v1/plans/project.html"),
])
def test_configure_existing_token_and_publish(publisher, tmp_path, monkeypatch, capsys,
                                              command, name, method, route):
    token = tmp_path / "server.token"
    token.write_text(TOKEN)
    monkeypatch.setattr(cli, "request", lambda *args: "ok")
    cli.configure(argparse.Namespace(endpoint="http://127.0.0.1:8787", token_file=str(token)))
    assert not (publisher / "publish.token").exists()
    capsys.readouterr()
    file = tmp_path / "résumé file.html"
    file.write_bytes(b"<h1>hello</h1>")
    def upload(endpoint, supplied_token, supplied_method, path, body, size):
        assert endpoint == "http://127.0.0.1:8787"
        assert supplied_token == TOKEN
        assert (supplied_method, path) == (method, route)
        assert body.read() == file.read_bytes()
        assert size == file.stat().st_size
        return "https://hub.example/a/result/file.html"
    monkeypatch.setattr(cli, "request", upload)
    cli.publish(argparse.Namespace(file=str(file), command=command, name=name))
    assert capsys.readouterr().out == "https://hub.example/a/result/file.html\n"


def test_http_transport_streams_and_does_not_follow_redirects(monkeypatch):
    class Connection:
        status = 201
        def __init__(self, host, port, timeout):
            assert (host, port) == ("hub.example", None)
        def request(self, method, path, body, headers):
            assert method == "POST"
            assert hasattr(body, "read")
            assert headers == {"Authorization": f"Bearer {TOKEN}", "Content-Length": "3"}
        def getresponse(self):
            return self
        def read(self, limit):
            return b"https://hub.example/a/id/file.txt\n" if self.status == 201 else TOKEN.encode()
        def close(self):
            pass
    monkeypatch.setattr(cli.http.client, "HTTPSConnection", Connection)
    assert cli.request("https://hub.example", TOKEN, "POST", "/upload", io.BytesIO(b"abc"), 3).endswith("file.txt")
    for status in (401, 302, 413, 507):
        Connection.status = status
        with pytest.raises(cli.ClientError) as error:
            cli.request("https://hub.example", TOKEN, "POST", "/upload", io.BytesIO(b"abc"), 3)
        assert TOKEN not in str(error.value)


@pytest.mark.parametrize("endpoint", ["http://remote.example", "https://user:secret@hub.example", "https://hub.example/path"])
def test_endpoint_rejects_unsafe_configuration(endpoint):
    with pytest.raises(ValueError):
        cli.parse_endpoint(endpoint)


def test_missing_config_reports_setup_instruction(publisher, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["artifact", "push", "missing.txt"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    assert "run artifact configure" in capsys.readouterr().err
