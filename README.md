# Artifact Hub

Private artifact publishing for tailnet machines. One server stores immutable files on disk, records metadata in SQLite, and returns browser URLs. It listens only on `127.0.0.1:8787`; Tailscale Serve provides private HTTPS.

Publisher machines install the `artifact` CLI and configure it once. Agents then publish without handling URLs or tokens.

## Configure a publisher

Install from this project with Python 3.11 or later:

```bash
gh repo clone siure/artifact-hub
cd artifact-hub
python3 -m venv ~/.local/lib/artifact-hub/venv
~/.local/lib/artifact-hub/venv/bin/pip install .
mkdir -p ~/.local/bin
ln -s ~/.local/lib/artifact-hub/venv/bin/artifact ~/.local/bin/artifact
```

Ensure `~/.local/bin` is on `PATH`. Remote machines also need Tailscale access.

```bash
artifact configure --endpoint https://geekbox.tail3454ce.ts.net
```

Setup prompts for the token with hidden input and verifies authentication before saving `client.toml` and `publish.token` in `~/.config/artifact-hub/`. Files have permissions `0600` and the directory `0700`. Relative token paths resolve against that directory. `ARTIFACT_HUB_CONFIG_DIR` overrides the directory.

On the hosting machine, reuse the server token without copying or changing it:

```bash
artifact configure --endpoint http://127.0.0.1:8787 \
  --token-file ~/.config/artifact-hub/publish.token
```

Remote endpoints require HTTPS. Credentials are read internally and are never passed as subprocess arguments. Processes running as the same Unix user can still access the token file.

## Publish

After setup, the agent runs:

```bash
artifact push ./report.pdf
```

The successful response is only the private artifact URL:

```text
https://geekbox.tail3454ce.ts.net/a/ARTIFACT_ID/report.pdf
```

The server infers the content type from the filename. Use the appropriate extension. Commands print only the URL on success; failures print to stderr and exit nonzero.

Every upload creates a new immutable artifact, even when filenames are identical. Artifacts expire after the server-configured lifetime, which defaults to 30 days.

To change retention, set `ARTIFACT_HUB_DEFAULT_TTL` in the server's `~/.config/artifact-hub/service.env` and restart `artifact-hub.service`. The value is in seconds: `2592000` is 30 days. It applies to new uploads and new named-plan versions; existing artifacts keep their recorded expiry dates. Retention is configured on the server, not per publisher or upload.

## Named HTML plans

Named plans keep one readable URL across revisions. Each update creates an immutable artifact version internally and atomically moves the stable plan name to the new version.

```bash
artifact plan ./plan.html project-plan.html
```

The first upload and every later update return the same URL:

```text
https://geekbox.tail3454ce.ts.net/p/project-plan.html
```

Plan names must end in `.html`. Concurrent updates use last-write-wins behavior.

## Retrieve

Open the returned URL directly in a tailnet-connected browser. Images, HTML, PDF, text, Markdown, JSON, and CSV display inline. Archives, office documents, executables, and unknown formats download.

Download an artifact from another machine with:

```bash
curl -fLO "$ARTIFACT_URL"
```

Retrieval does not require the publisher token. Tailnet access and the unguessable artifact URL provide access control.

HTML artifacts may use self-contained JavaScript and CSS, but the browser security policy blocks external resources, network requests, workers, forms, popups, and same-origin access. Ordinary links may navigate the current tab to their source. SVG scripts are disabled.

## Server operations

Runtime locations:

- Data: `~/.local/share/artifact-hub/`
- Token and configuration: `~/.config/artifact-hub/`
- Installed environment: `~/.local/lib/artifact-hub/venv/`
- User services: `~/.config/systemd/user/artifact-hub*`

Useful commands:

```bash
curl -fsS http://127.0.0.1:8787/healthz
systemctl --user status artifact-hub.service
systemctl --user status artifact-hub-gc.timer
journalctl --user -u artifact-hub.service
systemctl --user start artifact-hub-gc.service
```

Reinstall or upgrade the server after source changes:

```bash
./scripts/install-user.sh
```

Run tests:

```bash
.venv/bin/pytest -q
```

See [`docs/tailscale-setup.md`](docs/tailscale-setup.md) for the private HTTPS setup.
