# GTX 1080 Core Transcription Setup

Use this path to run the core app locally on an NVIDIA GTX 1080 while SaaS, OpenSearch, Redis, dashboards, and backup services are ignored.

## Why this path exists

The default compose stack is ROCm/AMD-oriented and starts extra services. A GTX 1080 is an older Pascal GPU with 8GB VRAM, so it should use `faster-whisper` with CTranslate2 on CUDA and Pascal-safe compute types.

Do not use FP16-first settings on this card. Start with:

- `WHISPER_BACKEND=faster-whisper`
- `WHISPER_MODEL=small`
- `FORCE_GPU=true`
- `GPU_DEVICE_PREFERENCE=cuda`
- `GPU_COMPUTE_TYPES=int8,float32`

If `small` is stable, try `medium`. Only try `large-v3` after confirming VRAM and latency are acceptable.

## Host requirements

- NVIDIA driver compatible with CUDA 12 containers
- NVIDIA Container Toolkit installed
- Docker Compose v2 with GPU support

Quick host checks:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.1.0-runtime-ubuntu22.04 nvidia-smi
```

## Run the minimal backend + worker

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.gtx1080.yml up --build db migrations api worker
```

This starts the small runtime path:

- Postgres
- Redis, because the base API compose service depends on it
- Alembic migrations
- FastAPI API
- CUDA worker

OpenSearch, dashboards, Prometheus, Grafana, and backups stay out of the core path.

## Submit one video

```bash
curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","kind":"single"}'
```

Then watch the worker logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.gtx1080.yml logs -f worker
```

## YouTube reliability

The default GTX 1080 path keeps YouTube ingestion simple:

```env
JS_RUNTIME_CMD=deno
YTDLP_REQUIRE_JS_RUNTIME=true
YTDLP_CLIENT_ORDER=default,mweb,web_safari,ios,android,tv
YTDLP_TRIES_PER_CLIENT=1
PO_TOKEN_USE_FOR_AUDIO=false
PO_TOKEN_USE_FOR_CAPTIONS=false
```

If downloads fail because YouTube challenges or rate-limits the request, export browser cookies to `./cache/yt-dlp/cookies.txt` and set this in `.env`:

```env
YTDLP_COOKIES_PATH=/root/.cache/yt-dlp/cookies.txt
```

Only enable PO tokens if public-video downloads/captions keep failing after Deno and cookies are working.

## Speaker labels

Speaker labels use pyannote diarization and produce anonymous labels like `Speaker 1` and `Speaker 2`. This is diarization, not real identity detection.

To enable it:

1. Create a Hugging Face token at <https://huggingface.co/settings/tokens>.
2. Accept the model terms for `pyannote/speaker-diarization-community-1` on Hugging Face.
3. Add this to `.env`:

```env
HF_TOKEN=hf_your_token_here
ENABLE_DIARIZATION=true
DIARIZATION_DEVICE=cpu
CLEANUP_DELETE_WAV=false
```

`DIARIZATION_DEVICE=cpu` is the safe GTX 1080 default. It keeps faster-whisper on the GPU and avoids pyannote competing for the card's 8GB VRAM.
`CLEANUP_DELETE_WAV=false` preserves the 16 kHz WAV for the separate
diarization worker.

Do not enable CUDA diarization on the GTX 1080 with the current ML image:
Torch 2.11 cu128 does not support the card's `sm_61` architecture. CUDA remains
appropriate for the separate faster-whisper ingestion worker; diarization is CPU-only.

## Production diarization

Production does not continuously enable the `diarization` profile. It uses
offline, CPU-only allow-listed one-offs with the exact verified snapshot
`/root/.cache/hf/hub/models--pyannote--speaker-diarization-community-1/snapshots/3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`.
The worker has a 600-second media cap and CPU, memory, and PID limits. After a
credential rotation, the selected operator environment file (normally
`.env.prod`) supplies the scoped ignored `HASANARA_DIARIZATION_ENV_FILE` and
the operator runs only:

```bash
scripts/compose_prod.sh maintenance diarization-canary <video-uuid> --approved
```

The helper acquires one eligible row in a short PostgreSQL advisory transaction
and fences it with a UUID token. It runs exactly one CPU-only UUID for at most
20 minutes. The token remains durable through Docker quiescence and is cleared
only by the exact-token finalizer. It never auto-requeues: SIGKILL or host loss
intentionally leaves a marker that blocks every later canary until guarded,
exact-token/container-quiescence recovery is performed. Batches remain explicit
and failed historical rows are never retried automatically.

After an interrupted canary, discover the durable video/token marker without
reading credentials:

```bash
docker exec hasanara-db psql -U postgres -d transcripts -Atc \
  "SELECT id, diarization_state, diarization_error FROM videos WHERE diarization_error LIKE 'canary-%'"
docker ps -a --no-trunc --filter "label=hasanara.canary-token=<invocation-uuid>" \
  --format '{{.ID}}\t{{.Names}}\t{{.Status}}'
```

Do not recover while that exact-token container exists. Verify its name is
`hasanara-diarization-canary-<video-uuid>-<invocation-uuid>`, stop/remove only
that exact container if necessary, then run:

```bash
scripts/compose_prod.sh maintenance diarization-canary-recover \
  <video-uuid> <invocation-uuid> --approved
```

Recovery rejects any wrong token or remaining token-labelled/name-conflicting
container. It either finalizes an already successful canary or atomically clears
labels and resets only the exact failed/leased target to pending.

The pre-existing `hasanara_diarization` login is checked before every canary;
the helper never creates or modifies it. It must have only `CONNECT`, public
schema `USAGE`, `SELECT` on `videos.id`, `state`, `wav_path`,
`diarization_state`, `diarization_error`, `duration_seconds`, `updated_at`, and `created_at`, and
`UPDATE` on `videos.diarization_state`, `diarization_error`, and `updated_at`.
It must have `SELECT` on `segments.id`, `video_id`, `start_ms`, `end_ms`,
`text`, `speaker_label`, `confidence`, `avg_logprob`, `temperature`, and
`token_count`, plus `UPDATE` on `segments.speaker_label`; no other object,
sequence, schema-create, ownership, membership, or elevated role privileges.

## Tuning

Recommended first pass:

```env
WHISPER_MODEL=small
GPU_COMPUTE_TYPES=int8,float32
GPU_MODEL_FALLBACKS=small,base,tiny
WHISPER_BEAM_SIZE=3
WHISPER_VAD_FILTER=true
ENABLE_DIARIZATION=true
DIARIZATION_DEVICE=cpu
```

If stable and too slow/low quality, try:

```env
WHISPER_MODEL=medium
GPU_MODEL_FALLBACKS=medium,small,base,tiny
```

## Common failures

- `CUBLAS_STATUS_NOT_SUPPORTED`: usually means an unsupported compute type. Use `GPU_COMPUTE_TYPES=int8,float32`.
- GPU not visible in container: verify NVIDIA Container Toolkit and `docker run --gpus all ... nvidia-smi`.
- Out of memory: use `small`, reduce beam size, keep only one worker job.
