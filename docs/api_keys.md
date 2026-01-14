# API Keys and Rate Limiting

- Admin bypass: requests supplying `X-ADMIN-TOKEN` equal to the `ADMIN_TOKEN` env var bypass authentication and rate limits for management endpoints.
- Per-API-key rate limits: When creating an `ApiKey` you may set `rate_limit_per_minute` (0 = unlimited).
- Multi-worker deployments: set `REDIS_URL` and install the `redis` Python package to enable Redis-backed counters for distributed rate limiting.

CLI examples:

```bash
# create an ApiKey for project 1 with 60 requests/minute
python scripts/manage_api_keys.py create 1 --limit 60

# rotate the project's primary API key (updates project.api_key_hash)
python scripts/manage_api_keys.py rotate-primary 1
```

Redis (multi-worker deployments)

To enable distributed per-API-key counters across multiple worker instances, set `REDIS_URL` (e.g. `redis://localhost:6379/0`) and install the `redis` package:

```bash
pip install redis
```

When `REDIS_URL` is set the application will prefer Redis counters and fall back to the DB if Redis is unavailable.

Reverse proxy / HTTPS

- Ensure your reverse proxy sets the `X-Forwarded-Proto: https` header so the admin UI enforces TLS.
- Set `ENV=production` or `REQUIRE_HTTPS_FOR_ADMIN=1` to require HTTPS for the admin UI.

CSRF cookie behavior

- By default the app will set a CSRF cookie with `samesite=Lax` and `secure=True` in production. Control with env vars:
  - `ADMIN_CSRF_HTTPONLY` — set to `1` to mark CSRF cookie `HttpOnly` (JS cannot read it). Default: `1`.
  - `ADMIN_CSRF_SERVER_SIDE` — set to `1` to store CSRF tokens server-side in the `admin_csrf` table and validate them on use. Default: `0`.

The admin UI fetches a CSRF token from `/admin/apikeys/csrf` and uses it for subsequent admin POSTs. If `ADMIN_CSRF_HTTPONLY=1` the UI uses the JSON response token rather than reading the cookie.
