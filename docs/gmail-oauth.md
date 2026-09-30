# Real Gmail (Google OAuth 2.0)

WorkFlowOS can connect a **real** Gmail account through Google's official
OAuth 2.0 authorization-code flow and the Gmail REST API.

This is separate from `DEMO_MODE`. A step that names `gmail_read_email` always
means real Gmail; a step that names `gmail_demo_read_email` always means the
local stand-in. **There is no silent substitution between the two** — an
unconnected real provider fails with a clear, non-retryable error.

---

## Credentials

The OAuth client is read from:

```
backend/credentials/google-client-secret.json
```

This is the file Google Cloud Console gives you ("Download JSON"). It is
gitignored (`.gitignore` → `backend/credentials/`, `credentials/`) and is
never returned by any API.

`app/config.py` prefers `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` from the
environment and falls back to the file. Both the `installed` and `web` shapes
Google produces are supported. `GET /api/integrations/gmail/status` reports
which source was used in `credential_source`.

## Scopes — minimum required

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | search and read messages |
| `https://www.googleapis.com/auth/gmail.send` | send a message |

**`gmail.modify` is deliberately not requested.** WorkFlowOS never mutates your
mailbox. Duplicate suppression is done with the `integration_events` ledger
(`UNIQUE(provider, event_id, automation_id)`), not by marking mail as read.

## Redirect URI — register this exactly

```
http://localhost:8000/api/integrations/gmail/callback
```

Add it under **APIs & Services → OAuth consent screen → Authorized redirect
URIs** in Google Cloud Console. It targets the **backend**, because the code
exchange is a server-to-server call; Google redirects the *browser* there and
the backend then redirects to the frontend Settings page.

Override it with `GOOGLE_REDIRECT_URI` if you run the API on another port.

## Flow

```
Settings → Connect Gmail (Google OAuth)
   → GET /api/integrations/gmail/connect      (302 to accounts.google.com)
   → user consents (or cancels)
   → GET /api/integrations/gmail/callback     (Google redirects here with ?code=&state=)
   → state validated (CSRF/replay), code exchanged at oauth2.googleapis.com/token
   → tokens Fernet-encrypted into SQLite
   → 302 back to http://localhost:3000/settings?gmail=connected
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/integrations/gmail/connect` | browser entry point → 302 to Google |
| `GET` | `/api/integrations/gmail/callback` | handles Google's redirect |
| `GET` | `/api/integrations/gmail/status` | connection status (no secrets) |
| `POST` | `/api/integrations/gmail/disconnect` | revoke + delete tokens |
| `POST` | `/api/integrations/gmail/connect` | non-browser: returns `authorize_url` |
| `POST` | `/api/integrations/gmail/test` | authenticated `users/me/profile` call |

## Security

- `state` is a single-use nonce (10-minute TTL) — protects against CSRF and
  replay.
- Tokens are encrypted with **Fernet** before touching SQLite. The key comes
  from `INTEGRATION_TOKEN_KEY`, or a gitignored local key file is generated.
- `client_secret`, `access_token` and `refresh_token` never appear in any
  response, and the callback redirect URL carries only a reason code and a
  plain-language message.
- The credentials JSON is not served over HTTP (`GET
  /credentials/google-client-secret.json` → 404).
- Only allowlisted actions can run: `gmail_search_emails`,
  `gmail_read_email`, `gmail_send_email`.

## Error handling

| Situation | Behaviour |
|---|---|
| User cancels | `?gmail=cancelled`, nothing stored |
| Invalid/replayed/expired `state` | `?gmail=invalid_state` |
| No code returned | `?gmail=missing_code` |
| Google rejects the code | `?gmail=exchange_failed` |
| Google unreachable | `?gmail=unavailable` |
| No credentials file/env | `state: not_configured`, 409 on connect |
| Access token expired | refreshed silently with the refresh token |
| Refresh token revoked | `AuthenticationError` telling you to reconnect |
| Insufficient permission (403) | clear "permission denied" error |
| Not connected, action runs | step fails: "Gmail is not connected", not retryable |

## Actions in the workflow pipeline

```
Discover → Understand (Ollama) → Generate → Approve
  → Automation (gmail trigger)
  → Scheduler polls Gmail (real REST API)
  → integration_events (idempotent) → background_jobs
  → Worker → execute_draft → gmail_* action → step result
  → activity_events audit → analytics
```

An automation with `trigger_type: "gmail"` polls the real mailbox. An
automation with `trigger_type: "demo_gmail"` polls the local simulator. They
are separate providers with separate event ledgers.

## Testing

`backend/tests/test_gmail_oauth.py` — 33 tests. Google's endpoints are mocked;
no live Google call is made and none is claimed. Covers credential loading,
minimum scopes, redirect URI, state validation, cancellation, code-exchange
failure, token encryption at rest, status never exposing secrets, credentials
file not served, real-Gmail-fails-without-mock-fallback, token refresh success
and failure, 403 permission handling, and real/demo separation.

## Limitations

- Only `localhost` redirect URIs are practical for a local demo; Google
  requires HTTPS for other hosts.
- A user who has already granted consent may need to revoke the app in
  `myaccount.google.com/permissions` before a new refresh token is issued.
- Gmail search uses the standard query language; a crafted value is sanitised
  (quotes and control characters stripped) so it cannot inject operators.
- No attachment handling, no drafts, no thread management.
