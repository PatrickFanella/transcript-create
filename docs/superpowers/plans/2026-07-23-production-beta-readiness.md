# HasanAra Production Beta Readiness Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Recommended path:
> dispatch a fresh subagent per task, review each result with `review-quality`,
> then continue. Steps use checkbox (`- [ ]`) syntax for tracking. Do not commit
> unless the user explicitly requests a commit.

**Goal:** Turn the current HasanAra working tree into a deterministic,
evidence-gated release candidate for moderated testing and an invite-only beta
on the existing HasanAra Docker Compose host stack.

**Architecture:** Keep the application and public API contract unchanged. Add a
last-applied Compose release overlay that replaces build-time/local tags with
operator-supplied immutable image references, validate that exact stack before
deployment, gate release publication on the canonical automated suite and full
browser matrix, and make human-testing/rollout evidence explicit. External
ingress remains responsible for invite-only access because archive reads are
intentionally public within the application.

**Tech Stack:** Docker Compose, Bash, Python 3.11, Gitea Actions, pytest,
Playwright, FastAPI, React 19, PostgreSQL/WAL-G.

---

## Scope and release gates

In scope:

- the current uncommitted working tree as the candidate source;
- the `scripts/compose_prod.sh` HasanAra host variant;
- secret-safe Docker build contexts;
- immutable release images and release preflight;
- tagged-release automated verification and browser evidence;
- production-shaped staging, moderated sessions, private beta, defect triage,
  rollout, roll-forward, and evidence templates.

Out of scope:

- deploying to the live host from this coding session;
- reading, copying, committing, or rotating secret values on the user's behalf;
- replacing the external management ingress;
- changing public archive access contracts to add application-level invites;
- executing destructive analytics/session migrations without an operator;
- public launch before private-beta exit criteria pass.

Release entry is blocked until: secret-bearing local files are excluded from all
build contexts; exposed credentials are rotated; `make verify` and the full
desktop/mobile browser matrix pass for the same commit; target images are pinned
with full `repository@sha256:<digest>` references; a staging
restore and migration rehearsal passes; external invite-only ingress is proven;
and no S0/S1 defect is open.

## Planned file structure

**Create**

- `docker-compose.release.yml` — final immutable-image overlay for the exact
  HasanAra host stack.
- `scripts/release_preflight.py` — fail-closed source, image, Compose, and
  evidence preflight without printing environment values.
- `tests/test_release_deployment_contract.py` — static and functional release
  contract tests.
- `docs/deployment/private-beta.md` — staging, backup/restore, migration,
  rollout, health, access-gate, rollback, and evidence runbook.
- `docs/user-testing/private-beta.md` — moderated-session and beta protocol.
- `.github/ISSUE_TEMPLATE/beta-feedback.yml` — structured beta feedback and
  severity inputs without soliciting secrets.
- `.gitea/workflows/release.yaml` — trusted manual candidate validation plus
  RC/beta image publication, digest signing/attestation, evidence, and Gitea
  prerelease creation.

**Modify**

- `.dockerignore` — exclude cookies, credentials, local release state, backups,
  and deepwork data from every Docker build context.
- `scripts/compose_prod.sh` — append the release overlay and add a `preflight`
  subcommand while preserving the exact project/network/env selection.
- `docker-compose.hasanara.yml` — make diarization opt-in via a profile so the
  default beta stack does not start an unused heavyweight worker.
- `.github/workflows/release.yml` — remove the GitHub/GHCR-specific release
  workflow so Gitea cannot discover a second incompatible tag workflow.
- `.env.example` — document image-reference and optional-profile variable names
  with inert examples only.
- `docs/deployment/README.md`, `docs/deployment/production-checklist.md`,
  `docs/development/testing.md`, `docs/operations/production-readiness.md`,
  `docs/operations/pitr-s3.md`, `docs/MIGRATIONS.md`, and `docs/STATUS.md` — link
  and harden the new authoritative gates.
- `scripts/check_documentation.py` — validate the new canonical documents.

## Task 1: Lock secret-bearing files out of Docker contexts

**Files:** `.dockerignore`, `tests/test_release_deployment_contract.py`

- [x] Add a failing contract test that reads `.dockerignore` and requires at
  least these root-safe patterns:

  ```python
  REQUIRED_DOCKER_EXCLUDES = {
      ".env",
      ".env.*",
      "cookies*.txt",
      "*.cookies",
      ".blacktower/",
  }
  ```

- [x] Run
  `.venv/bin/python -m pytest tests/test_release_deployment_contract.py -q`
  and confirm the test fails because cookie/deepwork/release-state patterns are
  absent.
- [x] Add the required exclusions under a clearly labelled secret/local-state
  section in `.dockerignore`. Do not delete or open local secret files.
- [x] Re-run the focused test and verify it passes.
- [x] Record an operator blocker in `docs/deployment/private-beta.md`: existing
  values in `.env.prod` and `cookies.txt` must be rotated before staging or beta,
  even though the source fix prevents future image inclusion.

## Task 2: Make the HasanAra host stack immutable and fail closed

**Files:** `docker-compose.release.yml`, `scripts/compose_prod.sh`,
`docker-compose.hasanara.yml`, `.env.example`,
`scripts/release_preflight.py`, `tests/test_release_deployment_contract.py`

- [x] Add contract tests requiring `scripts/compose_prod.sh` to apply
  `docker-compose.release.yml` last and requiring these deployment images to be
  supplied as `repository@sha256:<64 lowercase hex>` references without any
  fallback:

  ```text
  HASANARA_API_IMAGE
  HASANARA_INGEST_IMAGE
  HASANARA_ML_IMAGE
  HASANARA_FRONTEND_IMAGE
  HASANARA_POSTGRES_IMAGE
  HASANARA_REDIS_IMAGE
  ```

- [x] Create `docker-compose.release.yml`. For `db`, `migrations`, `api`,
  `redis`, `worker`, `analytics-retention`, `summary-refresher`,
  `archive-intelligence-refresher`, `diarization-worker`, `frontend`, and
  `backup`, set the appropriate required image expression. Reset every inherited
  `build` block with Compose's existing `!reset null` convention. Use the API
  image for migrations/maintenance/refreshers, ingest image for the main worker,
  ML image for diarization, PostgreSQL/WAL-G image for DB/backup, and the
  dedicated frontend/Redis images for those services. Compose interpolates
  required variables before profile selection, so require the ML digest even
  when diarization is inactive.
- [x] In the same final overlay, force this production environment-file contract
  for migrations, API, worker, retention, both refreshers, diarization, and
  backup:

  ```yaml
  env_file: !override ["${HASANARA_ENV_FILE:?HASANARA_ENV_FILE is required}"]
  ```

  Force `ALLOW_SESSION_TOKEN_CONTRACT_MIGRATION: "false"` for ordinary release
  commands. The maintenance runbook may opt in only after its drain, backup, and
  explicit operator approvals.
- [x] Require a URL-unreserved `DB_PASSWORD`, use it for PostgreSQL,
  `PGPASSWORD`, and every internal `DATABASE_URL`, and force
  `ENVIRONMENT=production` plus `LOG_LEVEL=INFO` for every Python application,
  worker, and migration service. Document `openssl rand -hex 32` as the safe
  generation path; preflight rejects defaults and reserved characters without
  printing the value.
- [x] Add `profiles: [diarization]` to `diarization-worker`; document
  `COMPOSE_PROFILES=diarization` as the only optional beta profile. Preflight
  must reject the `full` profile rather than silently accepting additional
  floating third-party images.
- [x] Append the release overlay to the `COMPOSE` array in
  `scripts/compose_prod.sh`. Add a `preflight` command that delegates to
  `scripts/release_preflight.py`. Add `deploy` as an atomic guarded path: run
  preflight successfully, then use the same helper/environment to execute
  `up -d --no-build --pull always`. Reject unguarded state-changing passthrough
  such as `up`, `down`, `pull`, `build`, `create`, `start`, `restart`, `stop`,
  `rm`, and `run`; allow only explicitly classified read-only commands. Provide
  a clearly named, preflighted `maintenance` path for the operator-approved
  drain/migration commands documented in `docs/MIGRATIONS.md`.
- [x] Inspect all existing and stopped containers labeled for the `hasanara`
  Compose project. Strict preflight rejects services outside the desired active
  set so disabled `full` or `diarization` containers cannot survive a profile
  transition. Provide only the fixed
  `maintenance retire-disabled-profiles --approved` action to preflight and
  remove `opensearch`, `dashboards`, `prometheus`, `grafana`, and
  `diarization-worker`; its hidden allowance must still reject unknown services.
- [x] Implement `release_preflight.py` as pure validation plus subprocess
  orchestration. It must:
  - reject a dirty tracked/untracked source tree unless `--allow-dirty` is
    explicitly supplied for local rehearsal;
  - require a downloaded `release-images.json` with one schema:

    ```json
    {
      "schema_version": 1,
      "source_commit": "40-lowercase-hex",
      "images": {
        "api": "registry/repository@sha256:64-lowercase-hex",
        "ingest-cuda": "registry/repository@sha256:64-lowercase-hex",
        "ml-cuda": "registry/repository@sha256:64-lowercase-hex",
        "frontend": "registry/repository@sha256:64-lowercase-hex",
        "postgres-walg": "registry/repository@sha256:64-lowercase-hex",
        "redis": "registry/repository@sha256:64-lowercase-hex"
      },
      "services": {
        "db": "postgres-walg",
        "backup": "postgres-walg",
        "migrations": "api",
        "api": "api",
        "analytics-retention": "api",
        "summary-refresher": "api",
        "archive-intelligence-refresher": "api",
        "worker": "ingest-cuda",
        "diarization-worker": "ml-cuda",
        "frontend": "frontend",
        "redis": "redis"
      }
    }
    ```
  - compare repository `HEAD` with the manifest source commit so bind-mounted
    scripts/configuration cannot drift from the released images;
  - run the exact helper's `docker compose config --quiet` without echoing the
    rendered config;
  - capture `docker compose config --format json` stdout/stderr in memory,
    inspect the explicitly calculated active services, and require every image
    to equal the corresponding manifest digest;
  - never echo or persist rendered configuration, raw subprocess errors,
    environment dumps, tracebacks, or non-allowlisted missing values;
  - verify the external `management` and `dev` networks exist;
  - verify the exact bind sources `docker-volumes/dbdata`,
    `docker-volumes/redis-data`, `backups`, `data`, and `cache` exist;
  - report names of missing variables/files only, never values.
- [x] Add unit tests for digest validation, manifest/HEAD mismatch, service
  mapping, profile filtering, and redacted error output. A sentinel secret must
  not appear in captured stdout, stderr, logs, exceptions, or tracebacks. Mock
  subprocesses; tests must not read `.env.prod`.
- [x] Add inert examples and comments to `.env.example`; never add real image
  digests or credentials.
- [x] Add real Docker Compose integration contracts for default rendering,
  `COMPOSE_PROFILES=diarization`, rejection of `COMPOSE_PROFILES=full`, forced
  env files, and forced-false maintenance migration. Use temporary non-secret
  inputs and `config --quiet`; never print the rendered config.

## Task 3: Gate and publish the real release artifacts

**Files:** `.gitea/workflows/release.yaml`, `.github/workflows/release.yml`,
`Dockerfile.postgres-walg`,
`scripts/start_backup_scheduler.sh`, `tests/test_release_deployment_contract.py`

- [x] Restrict this workflow to RC/beta artifact tags such as `v*-rc.*` and
  `v*-beta.*`. Reject stable version tags and always create a prerelease or
  draft. Automated publication is only gate 1; staging/moderated entry,
  invite-beta entry, and public/stable release remain separate decisions.
- [x] Add a release-workflow contract test requiring jobs named `verify`,
  `cross-browser`, `images`, and `release`, with `images` depending on both test
  jobs and `release` depending on `images`.
- [x] Add a `verify` job using Python 3.11 and Node 20. It installs
  both `requirements.txt` and `requirements-dev.txt`, frontend/e2e lockfiles,
  and Chromium, then runs `make verify` with `PYTHON_BIN` pointing at the
  setup-python interpreter. Use setup-node directly; do not claim `mise` ran in
  Actions unless it is explicitly installed.
- [x] Add a fail-fast-false `cross-browser` matrix for Firefox, WebKit, Mobile
  Chrome, and Mobile Safari. Install the mapped browser and run the seeded
  `e2e/tests/archive-smoke.spec.ts` with the named Playwright project against
  the Playwright-managed Vite server; do not add an unused production build.
  Upload artifacts even on failure.
- [x] Replace the monolithic ROCm release image with a matrix that builds,
  application-library scans, pushes without rebuilding, resolves a digest, and
  attests these deployment artifacts:

  ```text
  api            -> Dockerfile.api
  ingest-cuda    -> Dockerfile.ingest.cuda
  ml-cuda        -> Dockerfile.cuda
  frontend       -> frontend/Dockerfile
  postgres-walg  -> Dockerfile.postgres-walg
  ```

  Publish each under a distinct `git.subcult.tv/subculture-collective/hasanara-*`
  package path and emit the exact digest in a role-named JSON artifact. A trusted
  manual validation run uses a unique validation tag; prerelease tags must not
  publish any `latest` alias.
- [x] Install `cron` and `rsync` in `Dockerfile.postgres-walg` so the scanned and
  attested image contains its complete backup runtime. The scheduler must never
  perform mutable package installation at container startup.
- [x] Download the five digest fragments in the release job and generate
  `release-images.json` with the Gitea source SHA, artifact roles, active-service
  mapping, and the repository-variable-provided full
  `repository@sha256:<digest>` Redis reference. Every `images` value uses the
  full reference schema above. Mark Redis as third-party (not attested by this
  repository). Attach the manifest to the prerelease.
- [x] Keep OS-vulnerability reports visible but block high/critical
  application-library findings. Do not describe package-type scanning as
  reachability analysis.
- [x] Make release creation wait for every matrix artifact and keep `-beta` or
  `-rc` Gitea releases marked prerelease.
- [x] Replace GitHub-only attestation and SARIF services with digest-first Gitea
  registry evidence: rescan the pushed `repository@sha256` reference, generate
  SBOM and SLSA predicate evidence, Cosign-sign and attest that digest, verify
  all three with the generated public key, and retain the verification output.
- [x] Run the focused contract test and generic YAML/static validation.
  `actionlint` does not understand Gitea's `gitea.*` contexts or `releases`
  permission and must not be treated as authoritative for this workflow.
- [ ] Before creating the first tag, dispatch the workflow on the protected
  `release/v0.1.0-rc.1` branch as the configured release operator. Require the
  canonical/browser jobs, five registry digest scans, Cosign verifications,
  manifest assembly, and evidence artifact to pass without creating a release.

## Task 4: Add the private-beta deployment and evidence runbook

**Files:** `docs/deployment/private-beta.md`,
`docs/deployment/production-checklist.md`, `docs/deployment/README.md`,
`docs/operations/production-readiness.md`, `README.md`, `docs/STATUS.md`,
`scripts/check_documentation.py`

- [x] Write a fail-closed runbook with these ordered gates:
  1. freeze commit and release digests;
  2. rotate local credentials/cookies and complete a secret scan;
  3. prove external invite-only ingress without changing app public routes,
     and prove the API/frontend host bindings cannot bypass that gate from the
     public internet (firewall/listener inspection plus a remote negative
     probe, with evidence but no sensitive network details in public artifacts);
  4. run automated and browser gates;
  5. on a separate staging host (the fixed names, ports, networks, and bind
     paths make co-location unsafe), restore PostgreSQL/media into empty
     isolated storage using a staging-only WAL-G bucket/prefix;
  6. verify a current recovery point, start only PostgreSQL, restore into empty
     storage, rehearse additive migrations separately, then start application
     writers; separately rehearse any maintenance-only
     session/analytics boundary;
  7. deploy exact digests with Watchtower/automatic updates absent;
  8. verify frontend/API health, CSP/cache headers, OAuth, search fallback and
     lag, ingestion leases/retries, retention, backup recency, and alerts;
  9. collect moderated/beta evidence;
  10. promote, hold, or roll forward using explicit criteria.
- [x] Require the staging restore evidence to include a WAL-G base backup plus
  WAL replay, followed by migration rehearsal before application writers start.
  A logical `pg_dump` restore alone does not prove the target PITR path.
- [x] Define executable media recovery before writers: select a timestamped
  `/backups/media` source and matching checksum manifest, verify source checksums,
  restore into empty isolated storage with deletion semantics, compare file
  counts and file-byte totals, and require a checksum dry-run with no
  differences. Record the source and results without exposing sensitive paths.
- [x] Include an evidence packet template containing commit, image digests,
  verification run/artifacts, secret-rotation attestation (never values),
  staging restore timings/integrity, migration head, ingress proof, health
  screenshots/queries, moderated results, defect burn-down, beta observation
  window, known accepted S2/S3 issues, owner, and decision.
- [x] State the analytics scrub/session rotation boundary and hash-only migration
  boundary by linking the canonical runbooks and summarizing distinct recovery:
  ordinary additive rollout may restore prior application digests; after either
  maintenance boundary, old images are forbidden and recovery is roll-forward
  only.
- [x] Update `docs/MIGRATIONS.md` so the ordinary release overlay's forced-false
  default is explicit and only the isolated maintenance command may override
  it after approvals.
- [x] Replace any unquiet `docker compose ... config` instruction in
  `docs/operations/pitr-s3.md` with `scripts/compose_prod.sh preflight` or
  `config --quiet`; rendered configuration may contain expanded secrets.
- [x] Update canonical indices/checklists and add the document to
  `scripts/check_documentation.py`.
- [x] Run `python3 scripts/check_documentation.py` and require no missing links or
  status metadata.

## Task 5: Define moderated testing and the invite-only beta

**Files:** `docs/user-testing/private-beta.md`,
`.github/ISSUE_TEMPLATE/beta-feedback.yml`, `docs/STATUS.md`,
`scripts/check_documentation.py`

- [x] Define a 5–8 person moderated cohort spanning frequent HasanAbi VOD
  viewers, researchers/clippers, and at least one keyboard or assistive-
  technology user. Do not collect protected traits that are unnecessary for the
  test.
- [x] Require separate recorded manual keyboard and screen-reader passes over
  the three core tasks. These may be completed by different consenting
  participants, but automated axe checks or a keyboard-only pass do not replace
  screen-reader evidence.
- [x] Add a consent/opening script: explain purpose, voluntary participation,
  data/recording choice, pseudonymous analytics, no password/token sharing, and
  participant control to stop.
- [x] Add task-based scenarios without UI instructions. Measure these three core
  tasks for every participant:
  1. find a recent VOD and explain what is available;
  2. locate a quote/topic and provide a timestamped citation;
  3. verify the moment in playback and recover from a no-result query;
  Rotate these secondary tasks across the cohort rather than requiring all from
  every participant: compare timeline/opinion-history evidence; save/export an
  every-mention collection; inspect account/session controls; complete a core
  flow with keyboard or assistive technology.
- [x] Capture per task: unaided/assisted/failed completion, time as diagnostic
  context, observed path, error/recovery, confidence in the citation, and
  Single Ease Question score. End with one value question and one missing-trust
  question.
- [x] Define severity:
  - S0: security/privacy breach, data loss/corruption, or unrecoverable deploy;
  - S1: auth, search, timestamp playback, citation, or core ingestion unusable;
  - S2: major workflow/accessibility degradation with a workaround;
  - S3: polish, wording, or low-impact documentation issue.
- [x] Define moderated exit: zero S0/S1, at least 80% unaided completion on each
  core task, median ease >= 5/7, no repeated common core-task failure, no
  critical/serious accessibility finding, and every S2 accepted with an
  owner/date.
- [x] Define beta entry/exit: 20–50 invitees only after moderated exit; daily
  triage; at least seven consecutive stable days; no open S0/S1; restore
  evidence remains current; S2s are fixed or explicitly accepted; public launch
  requires a separate decision. A stable day is a recorded daily pass of named
  health, search-lag, worker-lease, and backup checks. Any S0/S1 or critical
  check failure resets the seven-day count.
- [x] Add structured issue fields for scenario, expected/actual result, impact,
  browser/device, accessibility context, request ID, and consented attachment.
  Treat GitHub as internal triage intake—participants may report through the
  moderator or beta channel and need no GitHub account. Warn users never to
  paste cookies, OAuth codes, API keys, or personal data.
- [x] Link the protocol from the authoritative status/deployment docs and rerun
  documentation validation.

## Task 6: Verify, review, and freeze the candidate

**Files:** all files changed by Tasks 1–5

- [x] Run focused release-contract and documentation checks.
- [x] Run frontend workflow/YAML lint if available and render the synthetic
  release Compose stack with `config --quiet`.
- [x] Run the canonical gate:

  ```bash
  TEST_POSTGRES_PORT=55433 mise exec node@20 -- \
    env PYTHON_BIN="$PWD/.venv/bin/python" make verify
  ```

  The runner did not have `mise`; the same gate completed with isolated
  Node 20.20.2 via `npx --package node@20` and Python 3.11.15.

- [ ] Run the pre-release Firefox, WebKit, Mobile Chrome, and Mobile Safari
  matrix locally where supported or retain passing CI artifact links in the
  evidence packet.
- [ ] On the target GTX 1080 staging host, smoke-test the released ML image for
  required imports, GPU visibility, and one bounded worker/diarization job before
  enabling the optional profile. This validates the move from the host-specific
  overlay image path to the published `Dockerfile.cuda` artifact.
- [ ] On the same target host, smoke-test the default CUDA ingest image for
  required imports, GPU visibility, and one bounded transcription job before
  moderated testing; this is required even when diarization stays disabled.
- [x] Ask an operations reviewer to check immutable deployment, backup/restore,
  migration, ingress, health, and roll-forward boundaries.
- [x] Ask a testing reviewer to check task neutrality, consent, accessibility,
  defect severity, and entry/exit criteria.
- [x] Ask an oracle reviewer for final maintainability/YAGNI and release-risk
  review. Fix all blockers and important findings.
- [x] Mark only coding-session-verifiable gates complete. Leave credential
  rotation, external ingress proof, staging restore, live migration, moderated
  sessions, beta observation, and release decision explicitly pending for the
  operator.

## Acceptance criteria

- No known local credential/cookie file can enter a Docker build context.
- The exact HasanAra production helper applies an immutable-image overlay last
  and fails closed on floating target images.
- A tag cannot publish release artifacts before canonical and cross-browser
  gates pass.
- Published image families match every active custom image in the target stack
  and produce attestable digests.
- The release runbook distinguishes ordinary additive rollout from the two
  maintenance/roll-forward boundaries.
- Private beta cannot begin without external invite gating, restore proof,
  staging checks, zero S0/S1 defects, and a completed moderated gate.
- User testing is task-based, consent-aware, accessibility-inclusive, measurable,
  and tied to explicit beta/public-release decisions.
- `make verify` passes for the final source candidate; human/operator gates are
  visible and cannot be misreported as completed by automation.
