# Contributor onboarding

How a new contributor, and their AI coding assistant, gets access to Hyperlite and works
alongside the others without stepping on them. Read `CONTRIBUTING.md` first for the branching
model (`test` = development, `master` = production).

Nothing in this file is secret. Real addresses, tokens and keys are exchanged privately, never
committed.

## Principles

- Everyone works in **their own clone**, on their own machine or VM. Never share a working
  directory, a shell session, or an assistant session.
- Everyone has **their own credentials** with the **minimum rights**. Never pass a token,
  an SSH key, the APT signing key or an admin password to someone else.
- Every change goes through a **pull request into `test`** with a green CI. Nobody pushes to
  `master` or `test` directly.
- **One person publishes and updates production** at a time (see "Publishing").

## Checklist for a new contributor

### 1. GitHub

- The maintainer adds you as a collaborator with the **Write** role, not Admin. Branch
  protection (pull request and green CI required) cannot be bypassed with Write access.
- Create a **fine-grained personal access token** limited to this repository, with the
  minimum permissions: Contents (read and write), Pull requests (read and write), Metadata
  (read). Add Workflows (read and write) only if you change `.github/workflows`.
- Set an expiry date on the token. Never paste it into a chat, an issue or a file.

### 2. Private network access (Tailscale)

- Install Tailscale on your own machine and sign in with your **own** account.
- The maintainer shares the machines you need with your account (one node at a time, not the
  whole network) and limits what you can reach with an access control list.
- Nothing is exposed on the Internet: no port forwarding, no Tailscale Funnel.

Example access control list fragment, to be adapted and checked with the ACL editor preview
before saving. It lets people who were shared a node reach only the Hyperlite web interface:

```json
{
  "tagOwners": { "tag:hyperlite": ["autogroup:admin"] },
  "acls": [
    {
      "action": "accept",
      "src": ["autogroup:shared"],
      "dst": ["tag:hyperlite:8000"]
    }
  ]
}
```

### 3. Hyperlite accounts

- The maintainer creates a **named** account for you with the lowest role that fits your work
  (observer, or a custom role) and you enable two-factor authentication at first sign-in.
- For scripts and automation, create your own API token in *Account security*. It can be
  revoked on its own.
- Do not use or share the `admin` account.

### 4. Development environment

- Clone the repository and work from `test`:

  ```bash
  git clone https://github.com/twikles/hyperlite.git
  cd hyperlite
  git checkout -b feat/my-change origin/test
  ```

- The end-to-end suite (`docs/webui-test-matrix.md`) needs a Linux host with libvirt/QEMU. It
  uses port 8011, the `hyperlite-isolated` libvirt network and resources prefixed `e2e-`, so
  **two people cannot run it on the same host at the same time**. Use your own host or VM.
- Run the same checks as CI before opening a pull request (see `CONTRIBUTING.md`).

### 5. Working with an AI coding assistant

- Give the assistant its own credentials (your token, your SSH key). It acts with your
  rights.
- Keep its permission rules strict when it can reach shared infrastructure: no destructive
  Git commands, no deletion on production, no changes to shared hosts without you saying so.
- Read what it proposes before approving a merge, a deployment or a deletion. Automatic
  modes do not replace a review.
- The assistant reads `CLAUDE.md` for the project conventions. Keep that file accurate and
  free of private data.

## Co-maintainers (full access)

A co-maintainer has the same rights as the owner: GitHub Admin, a named Hyperlite `admin`
account, and access to the build and production hosts. The rights are the same, so the habits
must be too:

- **Own credentials, always.** Your own GitHub token, your own SSH key added to the hosts
  (`authorized_keys`, with a comment naming you), your own Hyperlite account with two-factor
  authentication. The audit log then says who did what. Never reuse another person's key,
  token or password, and never copy the APT signing key.
- **Stay on the pull request flow even though you could bypass it.** Administrators can
  bypass branch protection (the publishing hook needs that). Do not use it for normal work.
- **Look before you start.** Check open pull requests and recent commits so two people do not
  fix the same thing. Prefix branch names with your initials (`ab/fix-...`) to avoid clashes.
- **Announce operations on shared hosts.** Before restarting a service, updating production
  or running heavy tests on the build host, tell the others.
- **Publication and production updates are serialized by the software**, as a safety net, not
  as a plan: publishing takes a lock (a second publication waits for the first) and the update
  button refuses to start while another update is running (HTTP 409 naming who started it).
  Still agree on who does it.
- **End-to-end tests on a shared host:** give each person a different `E2E_PORT` (default
  8011) and run them one at a time, because they share the libvirt host.
- **Assistants act with your rights.** With full access, keep your assistant's permission
  rules strict for destructive actions on shared or production hosts, and read what it
  proposes before approving.

## Publishing and production

- Publishing (APT repository and ISO) is done by a hook on a single build machine, with the
  signing key that never leaves it. Only the maintainer runs it.
- Production is updated by one person at a time, after the release pull request
  (`test` into `master`) is merged and the published version is checked.
- Before touching a production host, announce it to the others.

## Ownership

Some work belongs to a specific person, for example a long-running feature branch. Ask before
changing it, and never rewrite or delete someone else's branch.
