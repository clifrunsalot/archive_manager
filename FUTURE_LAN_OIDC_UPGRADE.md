# Future LAN and OIDC Upgrade

This plan is intentionally deferred while Archive Manager is tested on one machine under the owner's credentials.

## Target architecture

```text
LAN browser
    |
    v
Caddy HTTPS reverse proxy
    |
    v
OAuth2 Proxy <-> Authentik OIDC provider
    |
    v
FastAPI API
    |
    v
Shared archive services
```

## Deferred implementation order

1. Deploy Authentik separately with protected database and application secrets.
2. Create an OIDC application, confidential client, allowed groups, and exact callback URL.
3. Configure OAuth2 Proxy with the issuer URL, client credentials, cookie secret, and redirect URL.
4. Configure Caddy with a trusted LAN certificate and a stable DNS/hosts name.
5. Keep browser-facing traffic on Caddy only; keep FastAPI, Qdrant, Ollama, and PaddleOCR private.
6. Replace the trusted identity-header assumption with a verified proxy/network boundary.
7. Add role-based permissions for administrative operations.
8. Add CSRF protection, rate limits, upload quotas, and proxy/API request limits.
9. Run two-user authorization tests from a second LAN device.
10. Add end-to-end login, logout, upload, query, lifecycle, and recovery tests.

## Configuration required later

- Authentik issuer URL
- OIDC client ID and client secret
- OAuth2 Proxy cookie secret
- Exact HTTPS callback URL
- LAN DNS or hosts entry
- Client-trusted certificate authority
- LAN firewall policy
- Approved user/group mappings

Never commit the OIDC environment file or any generated key.

## Current-state boundary

The local-only mode is the interim operating mode. It derives identity from the
host operating-system account only for loopback API requests and leaves strict
event authorization enabled. It must not be used as a LAN authentication
mechanism. Enable it with `ARCHIVE_LOCAL_ONLY=1` and bind FastAPI to
`127.0.0.1`.
