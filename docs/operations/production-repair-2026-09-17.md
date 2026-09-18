# Production repair — 17 September 2026

Tracking: [GitHub issue #639](https://github.com/filthyrake/vlog/issues/639).
This is supporting release evidence; GitHub Issues remain the tracker.

## Deployed state

Production was cut over with explicit authorization. The public, admin, worker API,
CPU transcoder, and transcription services now run the immutable application
release **80570be**, with Python 3.12 and signed upstream FFmpeg 9.0.1.

| Component | Running artifact | Access / limits |
| --- | --- | --- |
| Application and workers | `vlog-worker-cpu:80570be` | Non-root, read-only root, no capabilities, no privilege escalation |
| PostgreSQL | `vlog-postgres:17.11-bb56be4` | PostgreSQL 17.11, loopback port 55436, 2 CPUs / 4 GiB |
| Redis | `redis@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf` | Redis 7.4.11, loopback port 6379, persisted AOF |
| CPU transcoder | Same application artifact | 2 CPUs / 8 GiB, one quality at a time, database queue |
| Transcription | Same application artifact | 2 CPUs / 8 GiB, CPU Whisper medium / int8 |

Application image ID:
`sha256:daa66eb333d7e12c1f064f23829ffc2727effbce1bd83639813fe5e874aba30d`.
PostgreSQL image ID:
`sha256:b2f1aef49fa847b95c61e89996fa9371c5122a378c8789acee47dc70c73c9d08`.

PostgreSQL 13 is stopped and disabled; its original data remains in
`/var/lib/pgsql/data`. The intermediate PostgreSQL 17 container is stopped with
restart disabled. The final database data directory is
`/home/damen/vlog-production/postgres17-minimal-live`. Its application role has
no superuser, role-creation, or database-creation privileges.

The Redis credential was rotated; the previous credential was verified rejected.
Worker administration now has a fresh private secret. The old Intel/GPU deployment
is scaled to zero. The obsolete Kubernetes backup and cleanup jobs are suspended.
GPU hardware provisioning remains deferred by the user's CPU-first decision.

## Repairs and verification

- Fixed public search and CSP-compatible comments/ratings; synchronized Alpine CSP
  3.15.3. Public assets have content-versioned URLs, and HTML now revalidates so
  browsers do not retain obsolete asset references across deployments.
- Fixed DASH-to-HLS teardown, malformed H.264 codec metadata from initialization
  fragments, CPU codec reporting, and automatic-quality analytics validation.
- Fixed account sessions passing through legacy admin middleware, matching CSRF
  tokens, PostgreSQL logout/session cleanup, and conditional live-stream updates.
- Signed user-bound source downloads and blocked raw originals on both static
  mounts (#540). Chat rate limits survive reconnects; configured Redis uses an
  atomic shared window and fails closed (#560).
- Unified Python manifests around a hashed runtime lock and repaired frontend
  test/build checks and security-summary gating.
- Live verification exposed a schema mismatch: migrated databases require non-null
  engagement counters, while client-side defaults produced null inserts. Metadata
  now uses the same non-null server defaults as migration 031, with an async insert
  regression test.
- Full backend baseline: **1664 passed, no skips**. After the upload-default fix:
  **245 targeted admin/public/database tests passed**. HTML/media cache-policy
  regression: **1 passed**. Admin frontend: **103 passed**. Public DOM: **3 passed**.
  Admin and studio production builds passed. Ruff, generated asset/manifest checks,
  workflow YAML parsing, and whitespace checks passed.
- Python release audit and all three npm audits reported zero advisories. Existing
  FastAPI/Starlette deprecation warnings remain. Pa11y/Lighthouse command loading
  was checked; a comprehensive accessibility audit was not performed.
- Isolated containerized login, CSRF, upload, CPU encoding, playback, and analytics
  passed on PostgreSQL 17 before cutover.
- Repeated the real flow against production after both application/database
  switches. Disposable accounts, sessions, generated clips, and uploaded videos
  were removed. The final catalog retains **51 ready videos** and its original
  admin account; anonymous administration returns 401 and raw source access is denied.
- Browser proof: a fresh production upload played to **4.01 seconds**, ended with
  `readyState=4`, and had no media error. An existing 16-minute Porsche video also
  advanced to **14.70 seconds**, with `readyState=4` and no media error, before
  being paused. Live search returned **4 Porsche** and
  **3 Toyota** videos. Returning browsers holding pre-release HTML may need one
  reload; new document responses explicitly revalidate.
- Recovered the remaining failed transcription after repairing the model cache.
  The 17-minute Toyota Supra video completed in **1,551 seconds** on the bounded
  CPU worker, producing **2,685 words**. All **51 transcriptions are completed**.
  Its public caption URL returns HTTP 200 with a valid `WEBVTT` header and
  **21,927 bytes**. This is one CPU capacity measurement, not a throughput test.

## Container security

The original CPU image had 266 high/critical package/advisory records. Updating
FFmpeg and its base reduced that to eight distinct unresolved OS advisories.
The final shell-free application image omits those unused utilities/libraries,
retains Debian package/file provenance and licenses, and reports **zero high or
critical findings** for its shipped Debian components and Python packages.

The first official PostgreSQL 17 image also contained vulnerable OS utilities and
an outdated Go helper. An Alpine rehearsal was rejected because catalog sorting
changed. The deployed minimal Debian build preserves the original libc locale,
collation version, and catalog ordering; `ltree` was verified functional.
Its scan also reports **zero high or critical findings**. The running Redis image
reports **zero high or critical findings**. No blanket vulnerability exclusions
were introduced. CI now scans both application and PostgreSQL images.

Source-built FFmpeg and PostgreSQL are not covered by Debian package matching.
Their explicit upstream versions and source signature/checksum verification are
separate controls; future upgrades must update those pins and repeat verification.
The PostgreSQL runtime builds the official 17.11 source with TLS, ICU, LZ4, and Zstd,
without unnecessary PAM, LDAP, systemd, terminal, or privilege-switching utilities.

## Data safety and backups

The authorized PostgreSQL 13-to-17 rehearsal restored **56 public tables**, matching
all row counts and 51 videos. The initial private dump remains on rockyweb at
`/home/damen/vlog-staging/repair-20260917/database-rehearsal/snapshot.dump`
(directory 0700, dump 0600). Temporary restore containers and credentials were removed.

The production switch took a fresh backup with application writers stopped.
The final minimal PostgreSQL switch used a fresh physical clone and verified
**every public table's row count and sorted row-content fingerprint** before
reopening application writes. It preserved database locale and collation version.

`vlog-database-backup.timer` now runs daily at 02:00 UTC with up to five minutes'
jitter. It uses the final PostgreSQL endpoint and stores private custom-format
backups in `/mnt/nas/vlog-storage/backups/postgres17` (directory 0700, dumps 0600).
Files are published atomically after archive validation. Seven days of this
service's completed backups are retained; unrelated backups are untouched.
An initial post-cutover backup was **fully restored** inside an automatically removed,
network-isolated container: **51 videos and 56 public tables** verified.
A further backup including the recovered transcription completed successfully;
the backup service validated its archive before publication.

Unused build intermediates were pruned after builds finished. Tagged images,
volumes, source checkouts, and rollback data were retained; root free space was
approximately 26 GiB at the post-cutover check.

## Operations and rollback

- Release source: `/home/damen/vlog-staging/repair-20260917/release-80570be`.
- Private configuration, original units/environments, final snapshots, and
  verification metadata: `/home/damen/vlog-staging/repair-20260917/cutover-e775826`.
  Each service uses its own `.env` there; `application.env` is the shared operational
  configuration. The directory name identifies the initial cutover, not the final
  application version.
- Actual systemd units are in `/etc/systemd/system/vlog-*.service`.
  Their container names end in `-release`. Database container: `vlog-postgres17-final`.
- Check API health at ports 9000/9001 `/health`, and port 9002 `/api/health`.
  The worker API does not expose an unversioned root `/health`.
- Whisper weights persist under `/mnt/nas/vlog-storage/.cache/huggingface`.
  A pre-existing zero-byte medium-model blob prevented loading during recovery.
  It was replaced atomically from the existing host cache after verifying its
  1,527,906,378-byte content against SHA-256
  `9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae`.
  Preserve this cache across application releases; existence alone does not prove
  a cached model is complete.
- Run a backup with `sudo systemctl start vlog-database-backup.service`; inspect
  its journal and timer state. Credentials must not be printed into shell logs.
- For an application-only rollback, preserve the current units first, then restore
  the private `old/*-b67c59a.service` files for the five application services to their
  normal systemd filenames. Keep the **current** environment files and PostgreSQL
  endpoint. Reload systemd, restart services, and recheck health and playback.
- Never point services back at the retained PostgreSQL 13 or intermediate 17 data
  after new writes have arrived. Those are historical rollback snapshots. A
  database-runtime rollback must preserve the current data directory or reconcile
  subsequent writes before restoring an older snapshot.

No repository branch was pushed or merged during this production deployment.
