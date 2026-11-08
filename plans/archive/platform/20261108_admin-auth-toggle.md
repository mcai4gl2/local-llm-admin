# Optional admin login (LAN-trusted mode)

Status: complete (deployed and live-verified)
Component: platform (auth)

## Goal

Let the operator turn off the admin login with one env setting
(`ADMIN_AUTH_ENABLED=false`) for a trusted local network, while keeping the
current password/session/CSRF behaviour as the default.

## Design

- New setting `admin_auth_enabled: bool = True` in `Settings` (Admin section).
- When disabled, the `require_session` middleware trusts every request and
  injects a synthetic session (`{"csrf": ""}`) so pages and `/api/session`
  keep working; the CSRF check is skipped (nothing protects a cookie that no
  longer gates anything).
- `/login` (GET and POST) and `/logout` redirect to `/` when auth is disabled.
- The base template hides the "Log out" button via an `auth_enabled` context flag.
- Startup logs a warning when auth is disabled so the choice is visible in
  `admin.log`.
- Documented in `docs/DESIGN.md` §6/§13-area deployment notes and
  `ops/env.example`.

## Tests

- Auth disabled: pages render without a cookie, API returns 200 without a
  cookie, POST without a CSRF header succeeds, `/login` redirects to `/`,
  the logout button is absent from rendered pages.
- Default remains enabled (existing auth tests keep passing).

## Progress

- [x] config + app changes
- [x] template change
- [x] tests
- [x] docs (DESIGN.md, ops/env.example)
- [x] gates: pytest (3911 passed, coverage 96.49%), ruff, mypy

## Outcome

Deployed via `ops/deploy-mac.sh`; `healthz` OK after the service reload.
`ADMIN_AUTH_ENABLED=false` was then set in the Mac's env file and the service
restarted. Live checks: `GET /` 200 without a cookie, `GET /login` 303 to `/`,
`/api/session` returns `{"csrf":""}`, a POST without a CSRF header is accepted,
and the startup warning is in `admin.log`. A POST used in verification stopped
the engine; it was restarted and confirmed `serving` again.
