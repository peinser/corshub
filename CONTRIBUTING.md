# Contributing ✅

Thank you for contributing! This document explains how to get started, how to run tests and CI locally, and how to configure a Personal Access Token (PAT) as `GITHUB_TOKEN` so you can run GitHub Actions workflows locally using `act`.

---

## Quick start 🔧

- Fork the repository and create a branch for your change.
- Open a concise PR with a clear description of what you changed and why.
- Add tests and docs for new features or bug fixes.
- Run linters and tests locally before submitting the PR.

---

## Run GitHub Actions locally with act 💡

We provide GitHub Actions workflows for CI. The devcontainer includes `act` and other tooling; the only manual step is to provide a `GITHUB_TOKEN` (this is a Personal Access Token, PAT) so workflows that rely on `GITHUB_TOKEN` will run locally.

> **Important:** The PAT you create should be scoped to this repository only and you are responsible for creating, storing, rotating, and ultimately revoking this token. Never commit tokens to the repository.

### 1) Create a Scoped Personal Access Token (PAT) 🔐

- Go to GitHub → **Settings** → **Developer settings** → **Personal access tokens**.
- Prefer **Fine-grained token** (recommended) so you can limit access to **this repository only**.
- When creating the token, select repository access to **Only select repositories** and choose this repository.
- Grant the minimum permissions required to run workflows. Typical permissions:
  - **Actions**: Read & write (so workflows can run and update statuses)
  - **Contents**: Read & write (if workflows need to checkout or push changes)

If you must use a classic token, grant the **repo** (and **workflow**) scopes and restrict usage to the repo where possible.

### 2) Make the token available to `act` 🧪

You can provide `GITHUB_TOKEN` to `act` in several secure ways. Example options:

- Temporarily in your shell (recommended for quick runs):

  ```bash
  export GITHUB_TOKEN=ghp_xxx-your-token-here
  act -P <platform> -j <job-name>
  ```

- Pass it on the `act` command line:

  ```bash
  act -s GITHUB_TOKEN=${GITHUB_TOKEN}
  ```

### 3) Run workflows locally

Once `GITHUB_TOKEN` is available in your environment, run `act` as you normally would. Example:

```bash
act -P on-prem=harbor.peinser.com/library/github-actions-runner:latest -s GITHUB_TOKEN=$GITHUB_TOKEN
```

Or run a specific job:

```bash
act -j test
```

---

## Security & responsibility ⚠️

- You are responsible for creating, scoping, storing, and rotating your PAT.
- Store the PAT securely (for example, in a reputable password manager such as 1Password, Bitwarden, or your organization's secret store); avoid storing tokens in plaintext files on disk.
- Do **not** commit tokens or secret files to the repository.
- Revoke the token when no longer required.

---

## Contribution workflow (recommended) 🧭

1. Create a topic branch from `main` (or the appropriate branch).
2. Make small, focused commits with descriptive messages.
3. Run tests and linters locally.
4. Push your branch and open a Pull Request.
5. Address review comments, re-run tests, and keep changes small.

---

## Style & testing 📋

- Follow the coding style of the project.
- Add tests for bug fixes and new features.
- Ensure CI passes before requesting a review.

---

## Code of conduct ❤️

Respectful, constructive collaboration is expected. If you encounter issues, please follow the project's code of conduct.

---

Thank you for helping improve the project! 🎉
