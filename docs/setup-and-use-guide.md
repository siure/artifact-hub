# Artifact Hub setup and usage

Artifact Hub runs on one server. Each publisher installs the `artifact` command and configures it once. See [the README](../README.md#configure-a-publisher) for installation commands.

## Publisher setup

Remote publishers need Python 3.11 or later and Tailscale access. After installing the CLI, the operator runs:

```bash
artifact configure --endpoint https://geekbox.tail3454ce.ts.net
```

Enter the shared token at the hidden prompt. Setup verifies authentication before saving `~/.config/artifact-hub/client.toml` and `publish.token`. Keep tokens out of chat, prompts, Git and logs. Rotating the shared token requires updating each publisher and restarting the server, which loads its token at startup.

On the hosting machine, reuse the existing token:

```bash
artifact configure --endpoint http://127.0.0.1:8787 \
  --token-file ~/.config/artifact-hub/publish.token
```

The client configuration is separate from the server's `service.env`. It contains only `endpoint` and `token_file`. Relative token paths resolve against the client configuration directory. `ARTIFACT_HUB_CONFIG_DIR` overrides the default directory.

## Agent usage

```bash
artifact push ./report.pdf
artifact plan ./plan.html project-plan.html
```

Both commands print only the resulting URL on success and exit nonzero on failure. The CLI streams the file and handles authentication internally. Ordinary uploads create immutable URLs; named HTML plans keep the same URL across updates, with last-write-wins behavior.

Retrieval needs Tailscale access but no publisher token. Images, PDF, HTML, text, Markdown, JSON and CSV display inline; other formats download. HTML supports self-contained scripts and styles inside a restrictive browser sandbox.

## Server operations

- Configuration: `~/.config/artifact-hub/service.env`
- Token: `~/.config/artifact-hub/publish.token`
- Data and SQLite metadata: `~/.local/share/artifact-hub/`
- Installed package: `~/.local/lib/artifact-hub/venv/`

```bash
curl -fsS http://127.0.0.1:8787/healthz
systemctl --user status artifact-hub.service
systemctl --user status artifact-hub-gc.timer
journalctl --user -u artifact-hub.service
tailscale serve status
```

Reinstall after source changes with `./scripts/install-user.sh`. This restarts the service and preserves existing configuration and tokens. Run client setup separately on a new machine.

Default limits are 14 days of retention, 512 MiB per artifact, 25 GiB total storage and two concurrent uploads. Expired artifacts immediately return `404`; the hourly cleanup timer later removes files and metadata. Named plans expire when their latest version expires.
