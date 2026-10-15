# SRX Security Policy

## Reporting
Do not publish exploit details or secrets in public issues. Until a private security contact is configured for the repository, use the repository host's private vulnerability-reporting feature if available; otherwise contact the maintainer through a verified private channel.

## Required release gates
- Review and remediate `pip-audit` findings; document accepted exceptions with expiry.
- Run Bandit and Ruff; review findings rather than treating a green exit as proof of security.
- Build and inspect the container; run it as non-root and keep secrets out of the image.
- Configure a real OIDC issuer, audience, and JWKS URL before protected API use.
- Put the service behind TLS-terminating, trusted reverse proxy infrastructure; restrict forwarded headers to known proxy addresses.
- Use an external managed secret store for production credentials.
- Back up the database and rehearse restore before schema changes.
- Conduct independent penetration testing before public production exposure.

## Scope limitations
CI checks are automated baseline checks, not certification or a substitute for threat modeling, manual review, dynamic testing, or an independent penetration test.

## API token lifecycle (0.25.2)

API token management is authenticated through the existing OIDC/JWT layer and requires `srx:write` to issue/revoke and `srx:read` to list. Set `SRX_API_TOKEN_PEPPER` to a randomly generated secret with at least 32 bytes, stored outside the repository and database. Raw tokens are returned only at creation; the database stores an HMAC-SHA-256 fingerprint. Listing is owner-scoped by verified issuer and subject. Revocation is owner-scoped and audited. Tokens expire after 90 days by default; requested lifetime is limited to 1–365 days.

**Important limitation:** API tokens are not yet accepted as credentials for other API routes. This release only implements their management lifecycle. Do not describe the API-key authentication feature as enabled until a separate reviewed change implements route authentication, scopes, rate limits, pepper rotation, and tests.
