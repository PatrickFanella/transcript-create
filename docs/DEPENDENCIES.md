# Dependency Management

## Overview

This document explains the technical details of dependency structure for transcript-create.

**📚 For comprehensive dependency management guidelines, see: [Development > Dependencies Guide](./development/dependencies.md)**

The detailed guide covers:
- Adding and updating dependencies
- Security patch handling with SLA
- Automated updates with Dependabot
- Version policy and testing strategies
- Troubleshooting common issues

## Files

- **requirements.txt**: Primary dependencies with pinned versions
- **constraints.txt**: Full dependency tree for reproducible builds
- **Dockerfile**: Special handling for PyTorch with ROCm/CUDA support

## Dependencies Structure

### Core Dependencies (requirements.txt)

All production dependencies are explicitly pinned to specific versions:

- **API Framework**: FastAPI, Uvicorn
- **Database**: SQLAlchemy, psycopg
- **Media Processing**: yt-dlp
- **Transcription**: faster-whisper, openai-whisper
- **Diarization**: pyannote.audio (optional)
- **Auth**: Authlib
- **Billing**: stripe
- **Export**: reportlab

### Full Dependency Tree (constraints.txt)

Contains all transitive dependencies with exact versions for:

- Reproducible builds across environments
- Security auditing
- Compatibility verification

## GPU Support: ROCm vs CUDA

### Why Special Handling?

PyTorch and related packages (torch, torchaudio) have different builds for:

- **CUDA**: NVIDIA GPUs
- **ROCm**: AMD GPUs
- **CPU**: No GPU acceleration

These cannot coexist and must be installed separately based on the target hardware.

### Dockerfile Strategy

Each Dockerfile installs one audited runtime contract:

1. **Read the ML versions** from `requirements-ml-runtime.txt` and install the
   matching CPU, CUDA 12.8, or ROCm 7.1 wheels.

2. **Resolve general dependencies** under the audited Python 3.11 constraints.

   ```dockerfile
   pip3 install -c constraints.txt -r requirements.txt
   ```

3. **Fail the build** unless `pip check` and imports of Torch, TorchAudio,
   TorchCodec, and pyannote all succeed.

### Build Arguments

- `ROCM_WHEEL_INDEX`: URL to PyTorch wheels for specific ROCm version
  - Audited/default: `https://download.pytorch.org/whl/rocm7.1`

Example for different ROCm version:

```bash
docker compose build --build-arg ROCM_WHEEL_INDEX=https://download.pytorch.org/whl/rocm7.1
```

### For CUDA/NVIDIA

To use NVIDIA GPUs instead:

1. Change base image in Dockerfile:

   ```dockerfile
   FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04
   ```

2. Change PyTorch installation:

   ```dockerfile
   pip3 install --index-url https://download.pytorch.org/whl/cu128 \
       torch==2.11.0 torchaudio==2.11.0
   ```

3. Update docker-compose.yml to use nvidia runtime

### For CPU-only

For development without GPU:

```dockerfile
pip3 install --index-url https://download.pytorch.org/whl/cpu \
  torch==2.11.0 torchaudio==2.11.0 torchcodec==0.14.0
```

Set in .env:

```bash
FORCE_GPU=false
```

## Updating Dependencies

### Security Updates

1. Check for vulnerabilities:

   ```bash
   pip-audit -r requirements.txt --no-deps --disable-pip
   pip-audit -r constraints.txt --no-deps --disable-pip
   npm --prefix frontend audit --audit-level=high
   ```

2. Update specific package:

   ```bash
   pip install --upgrade package-name==NEW_VERSION
   pip freeze | grep package-name
   ```

3. Test thoroughly in development

4. Update both requirements.txt and constraints.txt:

   ```bash
   # In a clean virtual environment
   pip install -r requirements.txt
   pip freeze > constraints.txt
   ```

5. Run security scans again to verify

### Major Version Updates

When updating to new major versions:

1. Review changelog and breaking changes
2. Update in a development branch
3. Run full test suite
4. Check compatibility with ROCm/CUDA versions
5. Update documentation if needed

### Automated Scanning

GitHub Actions automatically runs security scans:

- On push to main/develop branches
- On pull requests
- Weekly schedule (Mondays at 9 AM UTC)
- Manual trigger via workflow_dispatch

Scans fail on high/critical severity vulnerabilities.

## Constraints File

The `constraints.txt` file captures the complete resolved dependency tree. This provides:

### Benefits

1. **Reproducibility**: Exact versions for all dependencies
2. **Security**: Complete audit trail of all packages
3. **Debugging**: Easy to identify which transitive dependency changed

### When to Update

Update constraints.txt when:

- Adding new dependencies to requirements.txt
- Updating existing dependency versions
- After security updates
- Before major releases

### How to Generate

```bash
# Create clean virtual environment
python3 -m venv /tmp/venv-constraints
source /tmp/venv-constraints/bin/activate

# Install requirements
pip install --upgrade pip
pip install -r requirements.txt

# Generate constraints (excluding GPU packages)
pip freeze > constraints.txt

# Manually remove or comment nvidia-* packages if present
# These conflict with ROCm builds
```

## Known Compatibility Issues

### PyTorch + NumPy

- PyTorch 2.4.x requires numpy < 2.4
- pyannote.audio has specific numpy version requirements
- Current pinned versions are tested and compatible

### Python Version

- Tested on Python 3.12
- Some dependencies (numba, llvmlite) may have version-specific wheels
- Use Python 3.12 in production for best compatibility

### ROCm Compatibility

- Base image: `rocm/dev-ubuntu-22.04:6.0.2`
- PyTorch ROCm wheels: version must match base image
- Verify compatibility: <https://pytorch.org/get-started/locally/>

## Troubleshooting

### Import Errors

If you see import errors after dependency updates:

```bash
pip check  # Check for conflicts
pip list --outdated  # Check for outdated packages
pip install --force-reinstall -r requirements.txt
```

### GPU Detection Issues

If GPU is not detected:

```bash
# Inside container
python3 -c "import torch; print(torch.cuda.is_available())"
python3 -c "import torch; print(torch.version.hip)"
```

Expected output for ROCm:

- `cuda.is_available()`: True
- `version.hip`: "6.0" or similar

### Build Failures

If Docker build fails on PyTorch installation:

1. Check ROCM_WHEEL_INDEX URL is accessible
2. Verify PyTorch version is available for your ROCm version
3. Try a different ROCm version or PyTorch version
4. Check for wheel compatibility with Python version

## Development Workflow

### Local Development (No Docker)

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -c constraints.txt -r requirements.txt
```

### Docker Development

```bash
# Build with current dependencies
docker compose build

# Rebuild specific service
docker compose build worker

# Force rebuild without cache
docker compose build --no-cache api worker
```

## Resources

- PyTorch Installation: <https://pytorch.org/get-started/locally/>
- ROCm Documentation: <https://rocm.docs.amd.com/>
- pip-audit: <https://github.com/pypa/pip-audit>
- Safety: <https://github.com/pyupio/safety>
- Gitleaks: <https://github.com/gitleaks/gitleaks>

## Questions?

For dependency-related issues:

1. Check this document first
2. Review Dockerfile for GPU installation logic
3. Check GitHub Issues for similar problems
4. Open a new issue with dependency versions and error output
