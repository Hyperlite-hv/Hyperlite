# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting: open the *Security* tab of the repository and choose *Report a vulnerability*. Include the affected version, a description, reproduction steps and the impact you expect.

You should receive an acknowledgement within a few days. Hyperlite is maintained by a small team, so fixes are made on a best-effort basis; there is no bug bounty.

## Supported versions

Hyperlite has no stable release yet. Only the latest published version receives fixes.

## Security model in short

- The service runs as **root** and controls libvirt, networks, storage and a host shell. Treat access to the dashboard as root access to the machine.
- Sessions use signed JWTs (HS256, 4 hours). The token is stored by the browser in `localStorage`, so an XSS bug would expose it.
- Every response carries a Content-Security-Policy (only scripts served by Hyperlite run; the inline scripts of the built page are allowed by hash), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, as defence in depth against XSS and clickjacking. `Strict-Transport-Security` is opt-in (`HYPERLITE_HSTS_MAX_AGE`), since most installations use a self-signed certificate.
- The interactive API documentation is off unless `HYPERLITE_API_DOCS=1`; `/health` reports host name and versions only to a signed-in caller.
- Passwords are hashed with bcrypt. Optional TOTP two-factor authentication and API tokens (stored as SHA-256 hashes) are available. Sign-in attempts are rate-limited per account and per IP address.
- Secrets stored in the database (SMTP password, OIDC client secret) are encrypted with a key held in `.env`. This protects against a leaked database file, not against an attacker with full access to the filesystem.
- Cluster nodes are reached over SSH with a dedicated key and trust-on-first-use host key checking; a changed host key is refused.
- The web interface is served over HTTPS with a self-signed certificate generated at first start.

## Known security-relevant limitations

- The appliance installer asks for the Linux `root` password. The Hyperlite `admin` initial password is random but is displayed on the physical console banner and stored in `/root/.hyperlite-initial-password`; change it after the first sign-in.
- Root SSH login with a password is enabled by the appliance installer.
- SSH connections to VMs and containers that Hyperlite creates itself do not verify host keys (they are new, local guests).
- There is no fencing for high availability, see the README.
- The dashboard loads fonts from Google Fonts.
- No third-party security audit has been performed.
