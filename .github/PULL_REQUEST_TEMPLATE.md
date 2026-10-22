## Summary

<!-- What does this change and why? -->

## How it was verified

<!-- Tests added or run; for hypervisor behavior, how you checked it on a real host. -->

## Checklist

- [ ] `ruff check .`, `ruff format --check .` and `pytest` pass
- [ ] `npm run lint` and `npm run build` pass (if the dashboard changed)
- [ ] Documentation updated, and a changelog fragment in `changelog.d/` (never `CHANGELOG.md` itself), when behavior changes
- [ ] `docs/proxmox-parity.json` updated when a feature Proxmox VE has changes (the row, and `Closes #` its issue)
- [ ] No secrets, private hostnames or IP addresses added
- [ ] API v1 only grows: no route, parameter or field removed or made required
