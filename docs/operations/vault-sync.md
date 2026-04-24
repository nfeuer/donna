# Vault Sync (WebDAV)

> Slice 12 — operator guide for the Obsidian vault and its WebDAV sync
> channel. See `docs/domain/memory-vault.md` for the narrative and
> `docs/reference-specs/memory-vault-spec.md` for the design spec.

## Overview

The vault lives on the homelab NVMe at `${DONNA_VAULT_PATH}` (default
`/donna/vault`) and is exposed over WebDAV by a dedicated Caddy
container (`donna-vault`). The existing Cloudflare tunnel (configured
out-of-repo on the homelab) proxies the public hostname to Caddy on
port `8500`.

Both human edits (over WebDAV) and agent writes (via `VaultWriter`)
land in the same on-disk git repo, so history reflects both.

## Bring-up

1. **Create the host directory** and make sure the user running Docker
   can write it:
   ```bash
   sudo mkdir -p /donna/vault
   sudo chown -R "$USER":"$USER" /donna/vault
   ```
2. **Set environment variables** in `docker/.env`:
   ```bash
   DONNA_VAULT_PATH=/donna/vault
   CADDY_VAULT_USER=donna
   CADDY_VAULT_PASSWORD_HASH=<bcrypt-hash>
   ```
   Generate the password hash without leaving plaintext on disk:
   ```bash
   docker run --rm caddy:2 caddy hash-password -p '<my-strong-password>'
   ```
3. **Copy the Caddyfile**:
   ```bash
   cp docker/caddy/vault.Caddyfile.example docker/caddy/vault.Caddyfile
   ```
   Adjust if you need a different listen port or a non-default vault
   mount path.
4. **Start the service**:
   ```bash
   docker compose -f docker/donna-vault.yml up -d
   ```
5. **Verify WebDAV**:
   ```bash
   curl -u "${CADDY_VAULT_USER}:<plaintext>" \
       -X PROPFIND http://localhost:8500/
   # Expect a 207 Multi-Status XML response.
   ```
6. **Restart the orchestrator** so it picks up the vault mount and the
   new tools register into the skill system:
   ```bash
   docker compose -f docker/donna-core.yml up -d --build
   ```

## Obsidian client setup

### Desktop (macOS / Windows / Linux)

Recommended: the official **Remotely Save** or **Remotely Sync**
community plugin, configured for "WebDAV (HTTP)" with:

- Server: `https://vault.houseoffeuer.com/` (or the direct
  `http://<homelab-host>:8500/` inside the LAN).
- Username: value of `CADDY_VAULT_USER`.
- Password: the plaintext you hashed in step 2.
- Root: empty (the full bucket is the vault root).

Obsidian's built-in Sync is a paid product and is not required.

### Mobile (iOS / Android)

The same Remotely Save plugin works in Obsidian mobile. On first run,
point it at the HTTPS URL and enable "Sync on start" + "Sync on exit".

## Safety notes

- The WebDAV endpoint is **not** exposed to the public internet except
  via the existing Cloudflare tunnel — the Caddy container binds to
  `8500` on the host only.
- Basic-auth credentials are stored as a bcrypt hash; rotate them by
  regenerating the hash and restarting the container.
- All writes made over WebDAV are captured by the git repo on the next
  agent write that touches the same file. To guarantee history for
  pure-human edits, run a periodic commit via cron on the host (not
  required for slice 12, but useful in day-to-day ops).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `403 Forbidden` on PROPFIND | Basic-auth creds wrong or hash not set. |
| `404 Not Found` on every path | Bind mount points at the wrong host dir. |
| Obsidian "Remotely Save" fails with TLS error | Cloudflare tunnel not proxying `vault.*`; use LAN HTTP URL. |
| Agent writes fail with `vault_root_parent_missing` | `DONNA_VAULT_PATH`'s parent does not exist on the orchestrator host — create it and restart the orchestrator. |
