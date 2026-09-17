# Production repair — 17 September 2026

Tracking: [GitHub issue #639](https://github.com/filthyrake/vlog/issues/639).
This document records supporting release evidence; GitHub Issues remain the tracker.

## What changed

- Restored the live transcription service's missing environment file. It remained
  running with its restart counter stable at 108492 after the repair. Public
  health and the 51 ready videos remained intact.
- Fixed public search and CSP-compatible comments/ratings, DASH-to-HLS teardown,
  invalid H.264 codec metadata from fragment initialization segments, CPU codec
  reporting, and analytics validation for automatic quality selection.
- Fixed account sessions passing through legacy admin middleware, matching CSRF
  tokens, PostgreSQL logout/session cleanup, and conditional live-stream updates.
- Added signed, user-bound VOD source downloads and blocked raw source files on
  both static video mounts (#540). Chat rate limits persist across reconnects;
  configured Redis uses an atomic shared window and fails closed (#560).
- Consolidated Python manifests around Python 3.12 and a hashed runtime lock.
  Fixed frontend tests and malformed admin markup. CI now requires full backend
  tests, frontend tests/builds, npm audits, and the Python dependency audit.
- Prepared CPU service limits: two cores, one quality at a time, 8 GiB memory.
  The CPU image uses signed upstream FFmpeg 9.0.1 with network protocols disabled,
  avoiding the older distro FFmpeg. A container service template runs the local
  worker with a read-only root, no capabilities, and no privilege escalation.
  The old automatic Kubernetes rollout is removed; GPU hardware remains deferred.

## Verification

- Full PostgreSQL/Redis backend suite: **1664 passed, no skips**.
- Admin frontend: **103 passed**; admin and studio production builds pass.
- Public DOM tests: **3 passed**, including the player fallback race.
- Python release dependency audit: no known vulnerabilities, no advisory exclusions.
- Root, admin, and studio npm audits: zero advisories after development-tool updates.
  Pa11y/Lighthouse command loading was checked; a comprehensive accessibility
  audit was not performed.
- Real isolated account setup, logout, login, CSRF, upload, CPU encoding, playback
  files, and analytics passed. The CPU container also passed this flow.
- Browser: the corrected DASH clip played to its 4.01-second end with readyState 4
  and no browser warnings/errors. Public search was separately checked through
  the changed frontend using read-only production API requests.
- Ruff, generated-manifest/vendor checks, workflow YAML parsing, and diff whitespace
  checks pass. Existing FastAPI/Starlette deprecation warnings remain.

## Remaining release gates

The application release has not been switched into production. The live checkout
still uses its previous Python environment. Updating source code alone would not
update that environment or the system FFmpeg binary.

The initial Debian 12 CPU image scan had 266 high/critical package/advisory records.
Debian 13 reduced this to 215 records / 47 distinct advisories. Building current
FFmpeg and removing its large distro dependency set reduced the image scan to
44 records / 8 distinct high advisories, with no published Debian fixes:

- CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 (util-linux)
- CVE-2026-54369 (ACL)
- CVE-2025-69720 (ncurses)
- CVE-2026-16742 (systemd)
- CVE-2026-9538 (Perl Archive::Tar)

These are scanner findings, not proof of exploitability in the worker. Source-built
FFmpeg is not covered by Debian package matching; its upstream version and signed
source are verified separately. No blanket advisory exclusions were added. The
strict container security gate remains red until these findings are patched or
reviewed for the exact deployed artifact and runtime restrictions.

Production PostgreSQL is **13.23**, schema revision **037**, with 51 ready videos
and no unfinished transcoding jobs at preflight. PostgreSQL 13 is unsupported
([upstream policy](https://www.postgresql.org/support/versioning/)). The upgrade
rehearsal script uses a consistent read-only snapshot and compares every public
table's row count after restoring to PostgreSQL 17.11. Its temporary restore
container is removed; the private backup and result report are retained.

After explicit authorization of the destination, the production-data rehearsal
passed: PostgreSQL **17.11** restored all **56 public tables**, and every table's
row count matched the consistent source snapshot, including **51 videos**.
The 499,963-byte backup remains on rockyweb at
`/home/damen/vlog-staging/repair-20260917/database-rehearsal/snapshot.dump`.
Verified directory mode **0700**, backup mode **0600**, removal of the temporary
restore container and credential file, all six live VLog services active, and
public API health reporting healthy database and storage checks after completion.
The restore endpoint was bound only to loopback. Metadata is recorded alongside
the dump in `result.json`; no production records were copied to this repository.

This proves a schema/data restore and matching row counts, not a production
migration or exhaustive data equality. Database ownership and ACLs were excluded
from the dump/restore; the final application role and grants require separate
setup. The live database remains PostgreSQL 13.23.

## Cutover and rollback

1. Private production backup and restore rehearsal completed with approval using
   `scripts/rehearse-postgres-upgrade.py`. A rehearsal snapshot is not a final
   cutover snapshot: later writes must not be lost.
2. Stage an immutable candidate checkout, Python 3.12 venv from `requirements.lock`,
   built admin/studio assets, and a tagged CPU image. Preserve the old checkout,
   venv, environment file, and exact systemd units for rollback.
3. Review the image findings and database upgrade result. Obtain action-time
   approval before restarting public services or changing the database target.
4. Stop ingestion/workers for the final backup if migrating the database. Restore
   into a supported database with a dedicated application role, compare data,
   and switch the application environment. Do not remove the old database.
5. Restart APIs individually and verify public health, catalog, search, login,
   and playback. Start one CPU worker and prove a fresh upload reaches ready.
6. Roll back application paths/units to the preserved release if checks fail.
   If writes have reached a new database, reconcile them before reverting its
   endpoint; blindly restoring an old dump would lose those writes.

GPU Kubernetes workloads have not been restarted or provisioned. No changes have
been pushed to GitHub or merged, and no new public application release has been
activated. The only live remediation so far is the transcription environment fix.
