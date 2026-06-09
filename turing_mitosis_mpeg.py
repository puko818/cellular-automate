"""
Reaction-Diffusion: Mitosis regime — MP4 export
Gray-Scott model with GFP fluorescence colormap.

Requirements:
    pip install numpy
    pip install numba          # optional — enables ~10-20x JIT speedup
    ffmpeg must be installed: https://ffmpeg.org/download.html
    (macOS: brew install ffmpeg | Ubuntu: apt install ffmpeg)
"""

import numpy as np
import subprocess
import argparse
import sys

try:
    from numba import njit, prange
    _NUMBA = True
except ImportError:
    _NUMBA = False

# ---------------------------------------------------------------------------
# Gray-Scott parameters — mitosis regime
# ---------------------------------------------------------------------------
Du, Dv = 0.16, 0.08
F, K   = 0.0367, 0.0649


# ---------------------------------------------------------------------------
# Numba JIT kernel — compiled on first call, ~10-20x faster than NumPy
# ---------------------------------------------------------------------------
if _NUMBA:
    @njit(parallel=True, cache=True)
    def _gs_step(u, v, u2, v2, Du, Dv, F, K):
        H, W = u.shape
        for y in prange(H):
            for x in range(W):
                yn = (y + 1) % H
                yp = (y - 1 + H) % H
                xn = (x + 1) % W
                xp = (x - 1 + W) % W
                lap_u = u[yp,x] + u[yn,x] + u[y,xp] + u[y,xn] - 4.0*u[y,x]
                lap_v = v[yp,x] + v[yn,x] + v[y,xp] + v[y,xn] - 4.0*v[y,x]
                uvv   = u[y,x] * v[y,x] * v[y,x]
                u2[y,x] = min(max(u[y,x] + Du*lap_u - uvv + F*(1.0 - u[y,x]), 0.0), 1.0)
                v2[y,x] = min(max(v[y,x] + Dv*lap_v + uvv - (F+K)*v[y,x],     0.0), 1.0)


def init(W: int, H: int, n_seeds: int = 60, rng_seed: int | None = None) -> tuple:
    rng = np.random.default_rng(rng_seed)
    u = np.ones((H, W), dtype=np.float32)
    v = np.zeros((H, W), dtype=np.float32)
    for _ in range(n_seeds):
        cx, cy = rng.integers(0, W), rng.integers(0, H)
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                x, y = (cx + dx) % W, (cy + dy) % H
                v[y, x] = 0.5 + rng.random() * 0.2
                u[y, x] = 0.25
    return u, v


def laplacian(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode='wrap')
    return p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * a


def step(u: np.ndarray, v: np.ndarray,
         buf_u: np.ndarray, buf_v: np.ndarray, n: int = 1,
         *, du: float = Du, dv: float = Dv, f: float = F, k: float = K) -> tuple:
    """Advance the simulation n steps using pre-allocated output buffers.

    Returns (u, v, buf_u, buf_v) with u/v pointing to the latest state
    and buf_u/buf_v available for the next call (double-buffer swap).
    """
    if _NUMBA:
        for _ in range(n):
            _gs_step(u, v, buf_u, buf_v, du, dv, f, k)
            u, v, buf_u, buf_v = buf_u, buf_v, u, v
    else:
        for _ in range(n):
            uvv = u * v * v
            np.clip(u + du * laplacian(u) - uvv + f * (1 - u), 0, 1, out=buf_u)
            np.clip(v + dv * laplacian(v) + uvv - (f + k) * v, 0, 1, out=buf_v)
            u, v, buf_u, buf_v = buf_u, buf_v, u, v
    return u, v, buf_u, buf_v


def to_rgb(v: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """GFP fluorescence colormap."""
    sig  = np.clip(v * 4.0, 0, 1).astype(np.float32)
    core = np.power(sig, 0.7)
    halo = np.power(sig, 2.5) * 0.35
    noise = rng.random(v.shape, dtype=np.float32) * 0.018

    r = (core * 30  + halo * 60  + noise * 255).clip(0, 255)
    g = (core * 245 + halo * 180 + noise * 255).clip(0, 255)
    b = (core * 60  + halo * 80  + noise * 255).clip(0, 255)

    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: ffmpeg not found.")
        print("  macOS:  brew install ffmpeg")
        print("  Ubuntu: sudo apt install ffmpeg")
        print("  Windows: https://ffmpeg.org/download.html")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Gray-Scott mitosis MP4 exporter")
    parser.add_argument("--width",       type=int,   default=1080,                 help="Frame width (px) — use 1080 for LinkedIn")
    parser.add_argument("--height",      type=int,   default=1080,                 help="Frame height (px)")
    parser.add_argument("--duration",    type=float, default=30.0,                 help="Video duration (seconds)")
    parser.add_argument("--fps",         type=int,   default=15,                   help="Frames per second")
    parser.add_argument("--burn_in",     type=int,   default=2000,                 help="Simulation steps before recording")
    parser.add_argument("--steps_frame", type=int,   default=8,                    help="Simulation steps per frame")
    parser.add_argument("--seeds",       type=int,   default=15,                   help="Number of seed blobs")
    parser.add_argument("--seed",        type=int,   default=None,                 help="RNG seed for reproducibility")
    parser.add_argument("--crf",         type=int,   default=18,                   help="ffmpeg CRF quality (0=lossless, 23=default, 18=high)")
    parser.add_argument("--cell_size",   type=int,   default=1,                    help="Upscale factor — sim runs at W/N × H/N, output stays W×H; larger = bigger blobs")
    parser.add_argument("--Du",          type=float, default=Du,                   help="Diffusion rate of U (food)")
    parser.add_argument("--Dv",          type=float, default=Dv,                   help="Diffusion rate of V (activator) — typically Du/2")
    parser.add_argument("--F",           type=float, default=F,                    help="Feed rate — how fast food replenishes")
    parser.add_argument("--K",           type=float, default=K,                    help="Kill rate — how fast activator decays")
    parser.add_argument("--out",         type=str,   default="turing_mitosis_lf.mp4", help="Output filename")
    args = parser.parse_args()

    check_ffmpeg()

    # LinkedIn requires even dimensions
    W = args.width  + (args.width  % 2)
    H = args.height + (args.height % 2)

    # Simulation grid — smaller than output when cell_size > 1
    cell_size = max(1, args.cell_size)
    sim_w = max(2, (W // cell_size) + ((W // cell_size) % 2))
    sim_h = max(2, (H // cell_size) + ((H // cell_size) % 2))

    n_frames = int(args.duration * args.fps)
    rng = np.random.default_rng(args.seed)

    backend = "Numba JIT" if _NUMBA else "NumPy (install numba for ~10-20x speedup)"
    print(f"Sim: {sim_w}×{sim_h} → output: {W}×{H} | cell_size: {cell_size} | {n_frames} frames @ {args.fps} fps | backend: {backend}")

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{sim_w}x{sim_h}",
        "-pix_fmt", "rgb24",
        "-r", str(args.fps),
        "-i", "pipe:0",
        "-vf", f"scale={W}:{H}:flags=neighbor",
        "-vcodec", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(args.crf),
        "-movflags", "+faststart",
        args.out,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    u, v = init(sim_w, sim_h, n_seeds=args.seeds, rng_seed=args.seed)
    buf_u = np.empty_like(u)
    buf_v = np.empty_like(v)

    gs = dict(du=args.Du, dv=args.Dv, f=args.F, k=args.K)
    print(f"Gray-Scott: Du={args.Du} Dv={args.Dv} F={args.F} K={args.K}")

    print(f"Burn-in: {args.burn_in} steps …")
    u, v, buf_u, buf_v = step(u, v, buf_u, buf_v, n=args.burn_in, **gs)

    print(f"Recording {n_frames} frames …")
    for i in range(n_frames):
        u, v, buf_u, buf_v = step(u, v, buf_u, buf_v, n=args.steps_frame, **gs)
        frame = to_rgb(v, rng=rng)
        proc.stdin.write(frame.tobytes())
        if (i + 1) % (args.fps * 5) == 0:
            print(f"  frame {i+1}/{n_frames}")

    proc.stdin.close()
    proc.wait()

    import os
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"Done — {args.out} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
