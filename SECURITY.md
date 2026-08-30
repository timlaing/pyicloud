# Security Policy

PyiCloud authenticates to Apple iCloud on your behalf. This means it handles
credentials (passwords, session cookies, and locally cached keychain data) and
talks to Apple's services with your identity. Security is important here.

## Your responsibilities

- Only use PyiCloud against **your own** iCloud account (see also the
  [Terms of Use](TERMS_OF_USE.md)).
- Protect the credentials you configure. A session password or cookie stored on
  disk should be treated like any other secret.
- Do not log secrets. When reporting a bug, redact passwords, session tokens,
  cookies, and any personally identifying data before pasting logs.
- Be aware that this library interacts with Apple's web services and that your
  use must comply with Apple's Terms of Service.

## Supported versions

Security fixes are applied to the latest release. Because the project version
is derived from git tags via `setuptools-scm`, always upgrade to the newest
release to get security fixes:

- Latest release: https://github.com/timlaing/pyicloud/releases
- PyPI: https://pypi.org/project/pyicloud

Older releases are not routinely patched; we encourage users to stay current.

## Reporting a vulnerability

Please **do not** report security vulnerabilities in the public issue tracker.
Instead, report vulnerabilities privately through **GitHub's private
vulnerability reporting**, which goes directly to the maintainers:

> **Report a security vulnerability** → **https://github.com/timlaing/pyicloud/security/advisories**

You can also use **GitHub Security Advisories** from the repository's
**Security** tab (if enabled for this repository).

### What to include

To help us respond quickly and accurately, please include:

- A concise description of the vulnerability.
- The affected component (e.g. authentication/HSA2, session/cookie handling,
  a specific service module) and affected versions.
- Steps to reproduce or a minimal proof of concept.
- Any relevant configuration.
- Impact and, if you know it, a suggested fix.

### What happens next

- We aim to acknowledge receipt of security reports within a reasonable
  timeframe and to keep you informed as we triage.
- We will coordinate on a fix and release before the issue is publicly
  disclosed wherever possible.
- We ask that reporters allow time for a fix and release before public
  disclosure, and credit the reporter if they wish.

## Scope

The following are considered in scope for security reports:

- Authentication, HSA2/second-factor, and session/cookie handling in the
  library (e.g. `pyicloud/base.py`, `pyicloud/session.py`,
  `pyicloud/hsa2_bridge.py`, `pyicloud/cookie_jar.py`).
- Secret handling, keyring integration, and file output of sensitive data.
- Vulnerabilities in code that could be triggered by crafted server responses
  (malicious/replayed data from network endpoints).

Out of scope:

- Social engineering, phishing, or attacks requiring physical/direct access to a
  user's machine.
- Vulnerabilities in third-party dependencies themselves (report those to the
  upstream project, though we do track dependency security advisories).
- Abuse of PyiCloud to access accounts the user does not own — this is a
  violation of the Terms of Use, not a security issue in the library.

## Security tooling

This project uses a number of automated checks to help keep the codebase secure:

- GitHub **CodeQL** analysis runs in CI ([`.github/workflows/codeql.yml`](.github/workflows/codeql.yml)).
- **SonarQube Cloud** reports security ratings and vulnerabilities
  (`sonar.projectKey=timlaing_pyicloud`).
- GitHub **Dependabot** monitors dependency updates and known-vulnerability
  advisories.
- `prek run --all-files` runs hooks including `detect-private-key` and
  `detect-secrets`-class checks to prevent committing secrets.

If you find a security issue in our dependencies rather than in this codebase,
please report it to the upstream project as well.
