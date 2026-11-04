# Container deployment

FastTrade is research-only and never places orders. Start on a private machine or VPN. The dashboard and `/api` endpoints have no API-key authentication; do not expose them to an untrusted network. The versioned `/v1` API requires configured hashed API keys and Redis rate limiting. Secrets belong in a secret manager or a protected environment file, not the image or source control.

## Local dashboard

Copy `.env.example` to `.env` and fill in the provider credentials you need. Then run:

```sh
docker compose up --build -d
```

Open `http://127.0.0.1:8765/`. Set `FASTTRADE_PROXY_PORT` to change the host port. `docker compose ps` shows the app health; `/healthz` is liveness and `/readyz` also checks configuration and Redis. No provider call is made by the health checks. The dashboard can make paid provider calls, including its automatic SPY signal.

The app runs as UID 10001. Named volumes hold the batch SQLite database and Redis rate-limit data across restarts; `docker compose down` stops the stack without deleting them. Back up the database consistently, for example with SQLite's backup API. `docker compose down --volumes` deletes this state. The Dockerfile copies `pyproject.toml` and `uv.lock` before application source, so source-only rebuilds reuse the dependency layer. The standalone image's default server binds only to its own loopback; Compose deliberately binds the app to its container network and publishes only the proxy on host loopback.

Compose gives the app 110 seconds to stop; uvicorn allows up to 100 seconds for requests to drain. A standalone `docker stop` needs an explicit timeout above 100 seconds (for example, `docker stop -t 110 CONTAINER`) to provide the same window. Requests or background work that exceed these limits can still be interrupted.

The local Caddy configuration tells the app that proxied requests are local. Its published port **must remain bound to 127.0.0.1**; never widen that binding or put another externally reachable proxy in front of it. Only the dedicated Caddy container is trusted for forwarded headers. The private API configuration below uses a different Caddyfile that preserves the real client address.

## HTTPS for the versioned API over a VPN

Use `compose.private.yaml` with the base file for remote access. This profile exposes `/v1` and the health endpoints; remote dashboard and `/api` requests remain blocked. Before starting, configure `FASTTRADE_BATCH_KEY_HASHES` with hashed keys, limits and budgets as described in [the API configuration](usage.md). Set `FASTTRADE_PRIVATE_IP` to an address actually assigned to the host's VPN interface, and `FASTTRADE_DOMAIN` to a DNS name whose public A/AAAA record points to the host's public address. Restrict HTTPS port 443 to VPN clients with the host firewall. Configure provider credentials server-side.

For Caddy's HTTP-01 certificate challenge, port 80 must be reachable from the public internet and forwarded to this host; set `FASTTRADE_ACME_IP=0.0.0.0` only after configuring the firewall and forwarding. Caddy uses Let's Encrypt and renews certificates in the persistent `caddy_data` volume. Port 80 serves challenges and HTTPS redirects; it does not expose the dashboard. A private DNS name alone cannot satisfy HTTP-01. If public port 80 is unavailable, use a DNS-01 provider-enabled Caddy build and its provider-specific configuration, or supply a certificate through a separately configured proxy. Do not disable TLS verification in clients.

```sh
export FASTTRADE_PRIVATE_IP=100.64.0.10  # replace with this host's VPN address
export FASTTRADE_DOMAIN=signals.example.com
export FASTTRADE_ACME_IP=0.0.0.0
docker compose -f compose.yaml -f compose.private.yaml up --build -d
```

The address and domain are examples, not usable defaults. Keep the base and private Compose files together in all subsequent commands, including `ps`, `logs`, and `down`. An allowlisted browser origin must be set exactly in `FASTTRADE_CORS_ORIGINS`; credentialed CORS is not enabled. API keys are sent in `X-API-Key`, not URLs. Test from outside the VPN that HTTPS is unreachable and, from a VPN client, that `/` is forbidden and an unauthenticated `/v1` request is rejected. On Docker Desktop, verify that the VPN address can actually be bound by Docker before relying on this setup.

Nginx or Traefik can terminate TLS instead, but must replace the local Caddy configuration, preserve the real client IP, and be the only trusted proxy; do not forward untrusted client-provided `X-Forwarded-*` headers. Their certificate and firewall configuration is deployment-specific. Do not publish the app or Redis container ports directly.

## Logs and configuration

Application request logs are JSON records with a generated request ID, method, status and duration; responses include the same `X-Request-ID` header, exposed to allowlisted browser origins. Records omit request bodies, headers and query strings. Other infrastructure or exception logs may contain additional details; protect log access. `/v1/usage` reports account budget reservations, available token counts and optional configured-rate token-cost estimates, not actual provider billing. Set `FASTTRADE_MODEL_PRICES` as described in [API usage](usage.md) to enable cost estimates.

`FASTTRADE_HOST` controls the application's network-mode checks, while the uvicorn `--host` and `--port` options control its actual socket. The supplied Compose command sets both appropriately; changing either independently can block requests or weaken the intended boundary. Compose uses `FASTTRADE_PROXY_PORT` for the local host port; the private profile uses ports 443 and 80. Set `FASTTRADE_ENABLE_OPENAPI=false` to disable documentation and `FASTTRADE_CORS_ORIGINS` to a comma-separated exact allowlist. The provided deployment uses one app process: SQLite state is shared on one persistent volume and Redis holds rate limits. Multiple replicas need shared durable storage and a coordinated deployment design.
