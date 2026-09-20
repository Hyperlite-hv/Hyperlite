# Contributing to Hyperlite

Thanks for your interest. Hyperlite is licensed under the PolyForm Noncommercial License 1.0.0 (see [LICENSE](LICENSE)). By submitting a contribution you agree that it may be distributed under that license and that the maintainer may relicense it as part of the project.

## Ground rules

- All repository content is **English**: code, comments, commit messages, documentation, user-facing text.
- The HTTP API and the database keep **French wire identifiers and values** (for example `nom`, `etat`, `memoire_mo`, `en_cours`, `observateur`). Do not rename them silently: they are part of the contract with the dashboard and with existing installations. Translate only what a human reads (messages, labels, comments). See [docs/api.md](docs/api.md).
- Branching model: `master` is production and `test` is the integration (development) branch.
  - Start every change from `test` on a short-lived branch (`feat/...`, `fix/...`, `docs/...`), open a pull request **into `test`**, and delete the branch after the merge.
  - Never push directly to `master` or `test`.
  - To release, open a pull request from `test` into `master` once `test` is validated (CI green and the end-to-end suite passing). Publishing (APT repository, ISO) only follows `master`.
  - Dependabot opens its pull requests against  (see ).
  - Urgent production fixes use a `hotfix/...` branch from `master`, and are merged back into `test` afterwards.
- New here, or bringing an AI assistant? Read [docs/onboarding.md](docs/onboarding.md) for the access checklist and the working rules.
- Keep pull requests small and focused. Explain *why* in the description.
- Do not commit secrets, real hostnames or IP addresses of private infrastructure.

## Development setup

Requirements: Python 3.11+, libvirt development headers (`libvirt-dev`, `pkg-config`, a C compiler) to build `libvirt-python`, and Node.js 20.19+ for the dashboard.

```bash
git clone https://github.com/twikles/hyperlite.git
cd hyperlite

python3 -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt

cd dashboard
npm ci
cd ..
```

Run a throw-away instance (HTTP, local only):

```bash
export HYPERLITE_SECRET_KEY=$(openssl rand -hex 32)
export HYPERLITE_INITIAL_ADMIN_PASSWORD='choose-a-password'
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
```

To work on the dashboard with hot reload, run `npm run dev` in `dashboard/` (it proxies the API to `https://127.0.0.1:8000`).

## Checks

Run these before opening a pull request; CI runs the same ones.

```bash
venv/bin/ruff check .            # lint (bandit-style security rules included)
venv/bin/ruff format --check .   # formatting
venv/bin/python -m pytest        # tests

cd dashboard
npm run lint
npm run build
```

Tests use a temporary database and never talk to a real libvirt daemon. If your change touches hypervisor behavior, also describe how you verified it on a real host.

## Code conventions

- Python: `ruff` enforces the style (line length 120). Log with `logging`, do not swallow exceptions silently, and validate every URL or path that comes from a user.
- SQL: bind parameters; interpolate only fixed fragments or allow-listed identifiers.
- Comments explain *why*, not *what*. Do not reference tickets, dates or people in comments.
- Frontend: function components and hooks, Tailwind for styling; keep user-visible strings in English.

## Reporting bugs and security issues

Use the issue templates for bugs and feature requests. For vulnerabilities, follow [SECURITY.md](SECURITY.md) and do not open a public issue.
