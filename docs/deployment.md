# Deployment

This is a light, stateless proxy to the Broad SpliceAI Lookup / Pangolin and Ensembl
services: no local models, no database, no data volume, no build step. Deployment is
therefore just "run the container behind a TLS-terminating reverse proxy".

## Transport

**Streamable HTTP is the only transport — there is no stdio entry point.**
`--transport` accepts:

| Mode | Serves |
|---|---|
| `unified` (default) | FastAPI host (`/health`) **and** the MCP endpoint at `MCP_PATH` (default `/mcp`), on one port. |
| `http` | The MCP HTTP transport. |

The app always serves plain HTTP on its port; **TLS is terminated at your proxy**
(nginx / Caddy / Nginx Proxy Manager), exactly like the sibling `-link` deployments.
Connect clients to the `https://` URL of the proxy.

## Registering the server with an MCP host

Hosted (Claude Code):

```bash
claude mcp add --transport http spliceailookup-link https://spliceailookup-link.genefoundry.org/mcp
```

Claude Desktop / claude.ai connectors (`claude_desktop_config.json`):

```json
{ "mcpServers": { "spliceailookup-link": { "type": "http", "url": "https://spliceailookup-link.genefoundry.org/mcp" } } }
```

Local development (HTTP on loopback only):

```bash
make dev   # serves http://127.0.0.1:8603/mcp
claude mcp add --transport http spliceailookup-link http://127.0.0.1:8603/mcp
```

Most callers should reach this server through the
[genefoundry-router](https://github.com/berntpopp/genefoundry-router) gateway instead,
where its tools surface as `spliceai_<tool>`.

## Request boundary

Every HTTP route is gated by **exact** Host and browser-Origin allowlists. This
backend is **unauthenticated by design** — the router / reverse proxy owns the trust
boundary — so it MUST NOT be published directly to the internet.

- **`ALLOWED_HOSTS`** — exact Host values, **no scheme and no port**. Default admits
  only `localhost`, `127.0.0.1` and IPv6 loopback. Write IPv6 as bare **`::1`**, not
  `[::1]`. A public deployment must add its reverse-proxy hostname explicitly.
- **`ALLOWED_ORIGINS`** — exact browser Origins, default `[]`. An empty list still
  permits non-browser requests, which carry no `Origin` header at all; it only closes
  the browser path.
- **Wildcards are rejected at configuration time** in both lists — the process fails
  to start rather than silently admitting everything.
- **`ALLOWED_ORIGINS` controls request admission; `CORS_ORIGINS` controls the browser
  response headers.** They are different knobs. Keep the two lists aligned for browser
  clients, or a request that was admitted will fail in the browser (or vice versa).

The production overlay sets all three for the public hostname, e.g.:

```yaml
SPLICEAILOOKUP_LINK_ALLOWED_HOSTS: '["localhost","127.0.0.1","::1","spliceailookup-link.genefoundry.org"]'
SPLICEAILOOKUP_LINK_ALLOWED_ORIGINS: '["https://spliceailookup-link.genefoundry.org"]'
SPLICEAILOOKUP_LINK_CORS_ORIGINS: https://spliceailookup-link.genefoundry.org
```

## Docker

The image listens on container port **8000** (GeneFoundry fleet standard). Three
Compose files in [`docker/`](../docker):

| File | Use |
|---|---|
| `docker-compose.yml` | Base / local. Publishes `127.0.0.1:${SPLICEAILOOKUP_LINK_HOST_PORT:-8603}:8000` — loopback-bound, and the **host** port is the tunable. |
| `docker-compose.prod.yml` | Hardening overlay, layered on the base file. |
| `docker-compose.npm.yml` | Self-contained Nginx Proxy Manager deployment (**not** layered): no published ports, NPM routes to the fixed container name on the shared external `npm_default` network. |

```bash
make docker-build && make docker-up      # base stack
make docker-logs                         # follow logs
make docker-down

# production, per the Container & Deployment Hardening Standard v1
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

The prod overlay pins the image by digest (`SPLICEAILOOKUP_LINK_IMAGE`, required),
drops the published host port in favour of `expose` behind the proxy, makes the root
filesystem read-only with a writable `/tmp` tmpfs, sets `no-new-privileges`, drops
**all** kernel capabilities, and bounds memory / CPU / PIDs.

> `ports: !reset []` — not `ports: []` — is what drops the base file's published port.
> Compose *merges* list fields across `-f` overlays, so a plain empty list would leave
> the base mapping in place. This is a footgun, not a style choice.

## Multi-worker deployments

Background tasks are backed by FastMCP's Docket. `DOCKET_URL` defaults to `memory://`,
which is in-process — correct for the single-process unified host, and **wrong** as
soon as you run multiple workers, because a task submitted to one worker is invisible
to the others. Set `SPLICEAILOOKUP_LINK_DOCKET_URL=redis://…` (or the FastMCP-native
`FASTMCP_DOCKET_URL`) for a multi-worker deployment.

See [Configuration](configuration.md) for every other environment variable.
