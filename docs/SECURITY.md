# OmniTrack — Security

This document describes what the backend actually enforces, where the code lives,
and what is deliberately out of scope. Every control below is covered by
`backend/tests/test_security_*.py` or `backend/tests/test_audit_chain.py`.

## 1. Threat model

| Asset | Threat | Control |
|---|---|---|
| Accounts | Credential stuffing / brute force | Per-IP rate limit + account lockout (§5) |
| Sessions | Stolen access or refresh token | 15-min access JWT, rotating refresh tokens with reuse detection (§3) |
| API | Viewer/operator or anonymous caller changing state | Deny-by-default route policy: every mutating route needs `admin` (§4) |
| Audit trail | Insider edits/deletes rows to hide actions | HMAC-SHA256 hash chain + signed tail anchor, verified at startup and every 24 h (§6) |
| Face crops, snapshots, audit metadata | Disk / backup theft | AES-256-GCM at rest (§7) |
| Server | SSRF via camera URL, path traversal via video path, pickle RCE via model name | Input safety validators (§8) |
| Detector | Adversarial perturbations | Measured with ART FGSM/PGD (§9) — measured, not mitigated |
| Secrets | Default / committed keys | Startup refuses weak secrets (§2) |

Out of scope: TLS termination (put the API behind a TLS reverse proxy and list it in
`TRUSTED_PROXIES`), host hardening, and DoS beyond the per-IP rate limit.

## 2. Secrets

`app/security/secrets_check.py` runs before anything else in the app lifespan. Startup
**fails** if `JWT_SECRET_KEY`, `AES_SECRET_KEY` or `AUDIT_HMAC_KEY` is empty, shorter than
32 bytes, a known historical default, or contains a placeholder marker (`change-this`,
`changeme`, …), or if the Postgres password in `DATABASE_URL` is a known default.
`ENFORCE_SECRET_STRENGTH=false` disables this for throw-away test setups only.

Generate a fresh set (writes `backend/.env`, which is git-ignored; existing non-secret
values are kept and secret values are never printed):

```bash
cd backend && .venv/bin/python scripts/generate_secrets.py
```

**Rotation notes**
- Rotating `JWT_SECRET_KEY` signs everyone out.
- Rotating `AES_SECRET_KEY` makes previously encrypted crops/metadata unreadable
  (the Audit page marks such rows "metadata unreadable"). Decrypt and re-encrypt first if you need them.
- Rotating `AUDIT_HMAC_KEY` makes the existing chain fail verification at entry 1. Archive
  and verify the old chain with the old key before rotating.
- Old git history contains committed placeholder JWT/AES keys, a default Postgres password and,
  in an early commit, a hard-coded admin password and JWT. Treat all of them as public and
  never reuse them.

## 3. Authentication and sessions

Code: `app/security/jwt_handler.py`, `app/routers/auth.py`, `app/security/dependencies.py`.

- Passwords: bcrypt (cost 12). Passwords longer than 72 bytes are SHA-256 pre-hashed instead of being
  silently truncated. Policy: 10–128 characters. Unknown usernames are checked against a dummy hash,
  so response timing does not reveal whether an account exists.
- **Access token**: HS256 JWT, `type=access`, `iss=omnitrack`, `aud=omnitrack-api`, 15 min
  (`ACCESS_TOKEN_EXPIRE_MINUTES`). A refresh token is rejected where an access token is expected,
  and the reverse.
- **Refresh token**: `type=refresh`, 7 days (`REFRESH_TOKEN_EXPIRE_DAYS`), with its `jti` stored in the
  `refresh_tokens` table (migration `004_security`). Each `/api/auth/refresh` revokes the
  presented `jti` and issues a new pair in the same *family*. Presenting a rotated token
  a second time is treated as theft: the server revokes the whole family and every session of that user, and
  writes a `REFRESH_REUSE` audit entry.
- `/api/auth/logout` revokes the presented refresh token. `all_sessions=true` revokes every one of the user's refresh tokens.
  `/api/auth/change-password` revokes all other sessions and returns a new pair.
- Every request re-loads the user from the DB, so the role and active flag always come from the DB, never from the JWT. A `ver` claim must
  match `users.token_version`, which is bumped on password change, role change, deactivation and
  logout-all, so outstanding access tokens die immediately.
- Bootstrap: the first registered account becomes `admin`, serialised with a Postgres advisory lock
  so two parallel registrations cannot both win. After that, `/api/auth/register` is closed unless
  `ALLOW_PUBLIC_REGISTRATION=true`, and admins create users from **Account & Settings**.
- Frontend (`frontend/src/services/api.js`): a 401 triggers one single-flight refresh and a
  replay of the request. If the refresh fails, the frontend clears the tokens and redirects to /login. `startSessionKeeper()`
  refreshes about 2 minutes before expiry (and when the tab regains focus), so that `?token=` media
  URLs and WebSockets stay valid.
- Query-string tokens (`?token=`) are accepted only on GET/HEAD, for `<img>`/`<video>`/WebSocket
  use. Mutating requests must use the `Authorization` header.

## 4. Authorization — deny by default

Code: `app/security/route_policy.py`, installed app-wide in `app/main.py` as a router dependency.

| Request | Requirement |
|---|---|
| `GET` / `HEAD` / `OPTIONS` | any authenticated user (viewer, operator, admin) |
| `POST` / `PUT` / `PATCH` / `DELETE` | **admin** |
| endpoint decorated `@public_endpoint` | nobody (login, register, refresh, logout, bootstrap, health) |
| endpoint decorated `@authenticated_action` | any authenticated user (only `POST /api/auth/change-password`) |

New routes are therefore admin-only for writes unless someone opts out explicitly. Read-only
POSTs owned by other modules (for example a search endpoint that takes a JSON body) are admin-only until they are marked
`@authenticated_action`.
`tests/test_security_routes.py` walks the **live** route table (including routers mounted later)
and fails on any public route not in the allow-list, any mutating route that does not resolve to `admin`,
or any route the policy dependency does not reach.

The SQLAdmin panel (`/admin`, `app/admin.py`) accepts admin users only. It shares the lockout counters,
writes `ADMIN_LOGIN*` audit entries, and uses a signed session cookie (`ADMIN_SESSION_SECRET`,
derived from the JWT secret if unset). Password hashes and embedding vectors are hidden there, camera stream URLs
(which may embed credentials) are excluded from list views, and audit and embedding rows are read-only.

## 5. Brute-force protection

- **Rate limit** (`app/middleware/__init__.py`, Redis sliding window with an in-memory fallback):
  10 requests/min on login, 30 requests/min on other `/api/auth/*`, 120 requests/min per API area. The client IP is the
  socket peer. `X-Forwarded-For` is honoured **only** when the peer is in `TRUSTED_PROXIES`
  (`app/security/client_ip.py`), so a client cannot rotate a spoofed header to dodge the limit.
  Responds `429` with `Retry-After`.
- **Lockout** (`app/routers/auth.py`): after `LOGIN_MAX_FAILURES`=5 consecutive failures the account
  locks for `LOGIN_LOCKOUT_MINUTES`=15. While it is locked, login answers `423` with `Retry-After`, even
  for the correct password. An admin can unlock early (**Account & Settings → Users**). Every failure, block
  and lock is written to the audit chain.

## 6. Tamper-evident audit chain

Code: `app/security/audit_chain.py` (pure functions), `app/services/crud.py` (`AuditService`),
`app/routers/security.py` (endpoints and scheduler).

- Each row stores `current_hash = HMAC-SHA256(AUDIT_HMAC_KEY, canonical_json(row) || previous_hash)`.
  The key never touches the database, so someone with DB write access cannot recompute a valid chain.
- Appends are serialised with a Postgres advisory lock, so concurrent writers cannot fork the chain.
- A signed **tail anchor** (`AUDIT_ANCHOR_PATH`, which holds the last id and hash plus an HMAC) detects deletion of
  the newest rows, which a plain hash chain cannot see.
- Verification: `GET /api/audit/verify` (admin) runs a full check. The scheduler runs it at startup and every
  `AUDIT_VERIFY_INTERVAL_HOURS` (24), and `GET /api/audit/status` returns the last result. A break reports
  the first bad row id and the reason (edited, deleted, inserted, reordered, or anchor mismatch).
- Test: `tests/test_audit_chain.py` builds a 1,000-entry chain. Every single-row edit, every deletion, every
  adjacent swap and random reorders are detected (100%) at the correct `broken_at`. So are edits
  whose hash an attacker recomputed without the key, tail truncation (via the anchor) and a wrong key.
- Audit metadata is additionally AES-GCM encrypted (§7).
- Rows written before migration `004_security` use the old unkeyed SHA-256 scheme. Anyone with DB write
  access could always rewrite them, and rows whose `user_id` was nulled by the old `ON DELETE SET NULL` FK no
  longer match their hash. (The FK is gone now, so deleting a user no longer touches audit rows.)
  `python -m app.security.audit_rebase --apply` does four things. It checks that every row before the first keyed row is legacy,
  verifies the keyed part, exports the legacy rows to `storage/security/audit_legacy_archive_<ts>.json`,
  and signs `base_id` into the anchor so verification starts at the first keyed row. Keyed rows can never be
  excluded: the base is always the first HMAC row, and the base, any keyed row below it, a deleted base row and a
  legacy-relabelled keyed row are all detected (`tests/test_audit_chain.py`).

## 7. Encryption at rest

Code: `app/security/encryption.py`.

- AES-256-GCM, a fresh 96-bit nonce per record, and a 128-bit tag. Records are `v2:` + base64(nonce‖ct‖tag); files
  are `OTENC2\0` + nonce‖ct‖tag.
- Associated data binds each ciphertext to its purpose. Files use their file name as AAD, so a blob cannot be
  swapped onto another record without failing authentication.
- The key is `AES_SECRET_KEY` as 64 hex chars or base64 of 32 bytes. Otherwise it is derived with HKDF-SHA256 from a ≥32-byte passphrase.
- Encrypted: audit metadata, face crops and snapshots written through
  `write_encrypted_image` and served decrypted to authorised users only.
- **Not encrypted: Re-ID embeddings.** pgvector's HNSW index must read raw vectors to search.
  Encrypting them would turn every Re-ID lookup into a full decrypt-and-scan. Embeddings are
  512-d appearance vectors, not images. They cannot be inverted to a face with any practical quality, but they
  are still personal data. Protect them with database access control, retention
  (`app/db/maintenance.py`) and encrypted disks/backups.
- Alert evidence (snapshot, thumbnail and clip per alert, `app/services/evidence.py`) is encrypted as files.
- **Not encrypted: uploaded source videos and operator recordings** (`FOOTAGE_DIR`, `data/videos/`). OpenCV,
  the synopsis builder, track trimming and HTTP range playback all need seekable plaintext. Treat that folder
  as personal data: every footage route needs a login, upload and trim are admin-only, and its disk and backups should be encrypted at the OS level.
- Legacy `gcm:`/CBC records are readable for migration only. Nothing new is written in those formats.

## 8. Input safety

Code: `app/security/input_safety.py`.

- **Camera sources** (`validate_camera_source`): only rtsp/rtmp/http(s), numeric webcams and local files are allowed.
  http(s) sources are **denied unless** their host or resolved IPs are in `CAMERA_SOURCE_ALLOWLIST`.
  This blocks SSRF through server-side stream fetches. Link-local and cloud-metadata addresses (169.254.0.0/16,
  100.100.100.200, fd00:ec2::254) are blocked even when allow-listed. Credentials in a URL are
  redacted (`redact_source`) before they are logged or returned by the API.
- **Video paths** (`resolve_video_file`): the path must resolve, after symlinks, inside `VIDEO_SOURCE_DIRS`. `..`, absolute
  paths outside the roots and symlink escapes are rejected.
- **Model names** (`validate_model_name`): basename only, an allow-listed suffix, and the file must exist in
  `MODEL_WEIGHTS_DIR`. This matters because `.pt` loading unpickles arbitrary code.
- **Uploads**: an extension allow-list, a `MAX_UPLOAD_MB` cap enforced while streaming (413), and a sanitised
  stored name prefixed with the camera id and a timestamp.
- **Responses**: every `/api/*` response sends `Content-Security-Policy: default-src 'none'; sandbox`,
  `nosniff`, `X-Frame-Options: DENY` and `no-store`.

## 9. Adversarial robustness (measured)

Code: `app/security/adversarial_eval.py`, which can be triggered from the **Security** page or run with
`python -m app.security.adversarial_eval --images N`. The evaluation uses the IBM Adversarial Robustness Toolbox
(FGSM and PGD, L∞, untargeted) against the deployed ultralytics person detector. The adversarial gradient comes from
the detector's own loss, and results are scored as person AP@0.5 on a COCO val2017 person subset. Two cheap
input-transformation defences (JPEG re-encoding and spatial smoothing) are scored for comparison. The report is written to
`storage/security/adversarial_report.json`.

**Pipeline smoke test only (not a benchmark).** yolo11n, CPU, 2 COCO val2017 images (ids 139 and 785, 3 person
boxes), ε = 8/255. PGD used only 2 steps of 2/255, so its effective L∞ was 4/255. With n = 3 boxes these figures show
that the attack path works end to end. They are **not** a statistically meaningful robustness estimate.

| person AP@0.5 | no defence | JPEG q75 | median 3×3 |
|---|---|---|---|
| clean | 0.916 | 1.000 | 0.916 |
| FGSM ε=8/255 | 0.467 | 0.577 | 0.663 |
| PGD (2 steps, L∞ 4/255) | 0.348 | 0.467 | 0.555 |

The 10-image run with default PGD steps (`python -m app.security.adversarial_eval --images 10`) was not completed
on the shared dev box, because the shared ML lock was busy. Run it, or better a larger `--images` on the GPU demo PC,
and replace this table with the numbers from `storage/security/adversarial_report.json`.

Even in this tiny sample the pattern is clear: invisible perturbations cut person AP by roughly half, and cheap input
transformations recover only part of it. In the deployment threat model the attacker
would need to inject pixels into the camera stream, which the network controls above (camera allow-list, admin-only
source changes) make the more realistic barrier.

## 10. Operational checklist

1. `cd backend && .venv/bin/python scripts/generate_secrets.py`, then restart. Never reuse keys from git history.
2. Put the API behind TLS and set `TRUSTED_PROXIES` to the proxy's address.
3. Set `CAMERA_SOURCE_ALLOWLIST` to your camera subnet.
4. Keep `ALLOW_PUBLIC_REGISTRATION=false`. Create the first admin, then add users from the UI.
5. Watch **Activity Log**: a red "chain broken" banner means investigate before trusting the log.
