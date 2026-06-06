# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Gray-Scott reaction-diffusion simulator (mitosis regime) that renders blob patterns as MP4 video using a GFP fluorescence colormap. It can be run as a CLI script, as a batch Snakemake workflow, or as a containerised Flask web app deployed on Azure Container Apps.

## Requirements

```
pip install numpy flask         # runtime
pip install snakemake           # only needed for batch workflow
ffmpeg must be on PATH          # for MP4 encoding
```

## Running

### CLI (single render)
```
python turing_mitosis_mpeg.py
python turing_mitosis_mpeg.py --seeds 15 --fps 15 --duration 30 --out generated_files/out.mp4
```

Key CLI arguments:

| Argument | Default | Effect |
|---|---|---|
| `--seeds` | 15 | Number of starting blobs |
| `--fps` | 15 | Output framerate |
| `--duration` | 30.0 | Video length in seconds |
| `--burn_in` | 2000 | Warm-up steps before recording |
| `--steps_frame` | 8 | Simulation steps per recorded frame |
| `--crf` | 18 | ffmpeg quality — lower = larger file, better quality |
| `--seed` | None | RNG seed for reproducibility |
| `--width/--height` | 1080 | Forced to even numbers (H.264 requirement) |

### Snakemake batch workflow
Runs all named configurations in `config.yaml` and writes outputs to `generated_files/`.
```
snakemake --cores 4            # run all configs in parallel
snakemake -n                   # dry-run (show what would execute)
snakemake generated_files/preview.mp4  # single target
```
Edit `config.yaml` to add or adjust named runs.

### Web interface (local)
```
python web/app.py              # starts Flask on http://localhost:5000
docker compose up              # build + run in Docker, same port
```

## Architecture

```
turing_mitosis_mpeg.py   — standalone simulation + ffmpeg pipe (no dependencies beyond numpy)
Snakefile                — orchestrates multi-run batch jobs via config.yaml
web/
  app.py                 — Flask server; spawns simulation as a subprocess per job
  templates/index.html   — single-page UI; polls /status/<id> every 3 s; downloads via /download/<id>
Dockerfile               — python:3.11-slim + ffmpeg + numpy + flask; CMD runs web/app.py
docker-compose.yml       — local Docker test; mounts ./generated_files as a volume
aws/
  deploy.ps1             — creates ECR repository, App Runner IAM role, and GitHub Actions IAM user
.github/workflows/
  deploy.yml             — builds Docker image, pushes to ECR, creates/updates App Runner service
```

### Simulation pipeline (turing_mitosis_mpeg.py)
1. `init()` — seeds float32 grids: U (food) = 1.0 everywhere, V (activator) = 0 except at N random 7×7 blobs
2. `step()` — applies Gray-Scott PDE; `laplacian()` uses `np.roll` with toroidal (wrapping) boundaries
3. `to_rgb()` — maps V concentration to GFP palette (black = low V = full food, bright green = active blob)
4. `main()` — opens an ffmpeg subprocess and streams raw RGB24 frames directly to stdin — no temp image files

**Gray-Scott parameters** (module-level constants, changing these selects different pattern regimes):
```python
Du, Dv = 0.16, 0.08    # diffusion rates — Dv is always half Du in this model
F, K   = 0.0367, 0.0649  # feed and kill rates — these specific values select the mitosis regime
```

### Web job lifecycle (web/app.py)
- `POST /run` — validates JSON args, assigns a hex job ID, launches `turing_mitosis_mpeg.py` in a daemon thread, returns `{job_id}`
- `GET /status/<id>` — returns `{status: queued|running|done|error}`
- `GET /download/<id>` — serves the MP4 as an attachment once status is `done`
- Jobs are stored in an in-memory dict (`jobs`); they are lost on container restart (acceptable for a demo)
- The subprocess uses `sys.executable` and sets `cwd=PROJECT_ROOT` so the script resolves correctly inside Docker

### AWS deployment (aws/deploy.ps1)
```
aws configure                   # once — enter Access Key, Secret, region, output format
cd aws
.\deploy.ps1                    # creates ECR repo, IAM role, IAM user; prints GitHub secrets
```
After adding the 5 printed secrets to GitHub (Settings > Secrets > Actions), every push to `main` triggers the GitHub Actions workflow which builds the image and pushes to ECR. App Runner auto-deploys when it detects the new image digest.

The App Runner service is configured with `AutoDeploymentsEnabled: true` — no manual update command needed after the first deploy. Instance size is 1 vCPU / 2 GB.
