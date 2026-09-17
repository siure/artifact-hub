# Tailscale setup

The Artifact Hub application is installed and running locally. Tailscale is the remaining system-level dependency.

## 1. Install Tailscale — requires root

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
```

Creates the protected directory used for the Tailscale APT signing key.

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/resolute.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
```

Installs Tailscale's official Ubuntu 26.04 signing key.

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/resolute.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
```

Adds Tailscale's official stable Ubuntu 26.04 package repository.

```bash
sudo apt-get update
sudo apt-get install tailscale
```

Refreshes package metadata, installs `tailscaled`, and installs the `tailscale` CLI.

## 2. Join the tailnet and delegate operation — requires root once

```bash
sudo tailscale up --operator=siure
```

Prints an authentication URL, joins this machine to the tailnet, and permits Unix user `siure` to manage Tailscale without sudo. If `tailscale up` is run again later, repeat `--operator=siure` because that flag is not retained across `up` invocations.

Verify:

```bash
tailscale status
tailscale ip
```

## 3. Publish Artifact Hub privately — no root after operator setup

```bash
tailscale serve --bg http://127.0.0.1:8787
```

Creates a persistent tailnet-only HTTPS reverse proxy to Artifact Hub. Do not run `tailscale funnel`; Funnel would make it public.

Check the generated URL:

```bash
tailscale serve status
```

If Tailscale asks you to enable HTTPS certificates, open the displayed admin-console URL, enable the setting, and rerun the Serve command.

## 4. Configure returned links

Use the exact HTTPS URL shown by `tailscale serve status`:

```bash
/home/siure/Code/artifact-hub/scripts/set-public-url.sh \
  https://geekbox.YOUR-TAILNET.ts.net
```

This updates the canonical URL in `~/.config/artifact-hub/service.env` and restarts the user service.

## 5. Firewall check

First test Artifact Hub from another tailnet device. Port 8787 must remain closed externally because the application is loopback-only.

If Tailscale HTTPS is blocked by UFW, run:

```bash
sudo ufw allow in on tailscale0 to any port 443 proto tcp comment 'Artifact Hub via Tailscale'
sudo ufw reload
sudo ufw status verbose
```

This permits HTTPS only through the Tailscale interface. It does not expose port 443 on Wi-Fi, Ethernet, or the public internet.

## 6. Remote publishing

Install the CLI using the [publisher setup instructions](../README.md#configure-a-publisher). Configure each trusted publisher once, entering its token at the hidden prompt:

```bash
artifact configure --endpoint https://geekbox.YOUR-TAILNET.ts.net
artifact push ./report.pdf
```

Only trusted publishers should receive the token. Browser and download clients need tailnet access and the returned artifact URL, but not the publisher token.

## Useful commands

```bash
tailscale serve status
tailscale serve status --json
tailscale serve reset
```

`reset` removes all Tailscale Serve configuration on this machine.
