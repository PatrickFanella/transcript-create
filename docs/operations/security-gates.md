# Blocking Dependency and SAST Gates

HasanAra blocks releases on reachable dependency advisories and high-severity
Python SAST findings. The canonical runtime gate uses Python 3.11 and Node 20.

Run the focused gates locally with:

```bash
python scripts/check_security_exceptions.py
pip-audit --local --skip-editable \
  --ignore-vuln GHSA-rrmf-rvhw-rf47
pip-audit -r requirements.txt --no-deps --disable-pip
pip-audit -r constraints.txt --no-deps --disable-pip
pip-audit -r requirements-ml-runtime.txt --no-deps --disable-pip \
  --ignore-vuln GHSA-rrmf-rvhw-rf47
bandit -r app/ worker/ -lll -ii
npm --prefix frontend audit --audit-level=high
```

The installed-environment pip-audit covers resolved transitive packages in
backend CI. The three manifest audits independently cover every exact direct,
full-snapshot, and image-specific ML runtime pin without invoking package build
hooks. Each CPU, CUDA 12.8, and ROCm 7.1 image runs `pip check`, imports Torch,
TorchAudio, TorchCodec, and pyannote, and is blocked by fixed high/critical
Trivy application-library findings. Operating-system findings are reported
separately; package-type scanning is conservative and is not described as
reachability analysis. Bandit blocks
high-severity findings with medium-or-higher confidence. The frontend gate
blocks high and critical npm advisories.

The Gitea release workflow builds and loads an image once, scans that local
artifact, and pushes it without rebuilding. It then resolves and rescans the
exact registry digest before Cosign-signing and attaching verified SLSA and SBOM
attestations. Gitea prerelease creation waits for every digest scan and
verification artifact. Kubernetes deployment is manual-only: it resolves the
requested tag, verifies provenance and application libraries for that exact
digest, and supplies the immutable digest to every Helm workload. The retired
GitHub/GHCR and duplicate production workflows must not be restored.

## Active exceptions

`GHSA-rrmf-rvhw-rf47` affects `torch==2.11.0` in the transcription and
diarization worker images. It requires local invocation of `torch.jit.script`.
HasanAra accepts audio/video input, never user model artifacts, and does not
call that compiler API; CI fails if the call appears. Model identifiers and
runtime configuration remain operator-controlled. The advisory is scored low
and has no patched release. A centralized UTC date and AST-based call check
causes CI to fail when the exception expires or the compiler call is introduced
through either qualified or imported-alias syntax.

- Owner: backend maintainers
- Approved: 2026-07-10
- Expires: 2026-08-09
- Required action: reassess upstream fixes and remove the exact ignore as
  soon as a compatible patched wheel is published

A future exception must identify the advisory, affected package and path,
reachability evidence, compensating control, owner, approval date, and an
expiry no more than 30 days later. CI exceptions must use the advisory's exact
identifier; blanket scanner suppression is not permitted.
