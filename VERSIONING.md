# Version workflow

This repository keeps Candice's `main` branch as the shared integration trunk.
Feature work must not be pushed directly to `main`.

## Branches

- `main`: shared, reviewed, production-ready history.
- `feature/<owner>-<topic>`: isolated development work, for example
  `feature/byron-crypto-pattern-lab`.
- `release/<version>`: optional short-lived stabilization branch used only
  while preparing a tested merge into `main`.
- `hotfix/<topic>`: urgent fixes created from the latest `main`.

Before starting or updating a feature branch, fetch `upstream/main` and merge
it into the feature branch. Resolve conflicts and run the full test checklist
on the feature branch. Merge through a pull request; never force-push `main`.

## Versions

Use semantic versions:

- Patch (`1.0.1`): compatible bug fix.
- Minor (`1.1.0`): backward-compatible feature.
- Major (`2.0.0`): breaking API, schema, or deployment change.

Pre-merge builds may use prerelease versions such as `1.1.0-beta.1`. After the
pull request is merged and the stable deployment is verified, create an
annotated tag such as `v1.1.0`. NAS image labels such as `dev-v21` are deployment
build identifiers, not product versions.

## Release checklist

1. Sync the feature branch with the latest `upstream/main`.
2. Review the diff and scan for `.env`, credentials, databases, logs, and user data.
3. Run backend tests and the frontend production build.
4. Verify the development deployment: initial K-line render, route-away/return
   render, period switching, exact and similar matching, probability chart,
   historical cases, and paper-trading forms.
5. Open a pull request and let the other maintainer review it.
6. Merge to `main`, deploy stable, verify health and visible asset versions.
7. Tag the verified merge and update `CHANGELOG.md`.
