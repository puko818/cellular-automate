"""
Reaction-Diffusion: Mitosis regime — MP4 export
Gray-Scott model with GFP fluorescence colormap.

Requirements:
    pip install numpy
    ffmpeg must be installed: https://ffmpeg.org/download.html
    (macOS: brew install ffmpeg | Ubuntu: apt install ffmpeg)
"""

import numpy as np
import subprocess
import argparse
import sys

# ---------------------------------------------------------------------------
# Gray-Scott parameters — mitosis regime
# ---------------------------------------------------------------------------
Du, Dv = 0.16, 0.08
F, K   = 0.0367, 0.0649


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
    return (
        np.roll(a, 1, axis=0) + np.roll(a, -1, axis=0) +
        np.roll(a, 1, axis=1) + np.roll(a, -1, axis=1) -
        4 * a
    )


def step(u: np.ndarray, v: np.ndarray, n: int = 1) -> tuple:
    for _ in range(n):
        uvv = u * v * v
        u = np.clip(u + Du * laplacian(u) - uvv + F * (1 - u), 0, 1)
        v = np.clip(v + Dv * laplacian(v) + uvv - (F + K) * v, 0, 1)
    return u, v


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
    parser.add_argument("--out",         type=str,   default="turing_mitosis_lf.mp4", help="Output filename")
    args = parser.parse_args()

    check_ffmpeg()

    # LinkedIn requires even dimensions
    W = args.width  + (args.width  % 2)
    H = args.height + (args.height % 2)
    n_frames = int(args.duration * args.fps)
    rng = np.random.default_rng(args.seed)

    print(f"Grid: {W}×{H} | {n_frames} frames @ {args.fps} fps | {args.duration}s")

    # Open ffmpeg pipe — raw RGB frames in, H.264 MP4 out
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{W}x{H}",
        "-pix_fmt", "rgb24",
        "-r", str(args.fps),
        "-i", "pipe:0",
        "-vcodec", "libx264",
        "-pix_fmt", "yuv420p",   # required for broad player compatibility
        "-crf", str(args.crf),
        "-movflags", "+faststart",  # web-optimised: metadata at front
        args.out,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    print(f"Burn-in: {args.burn_in} steps …")
    u, v = init(W, H, n_seeds=args.seeds, rng_seed=args.seed)
    u, v = step(u, v, n=args.burn_in)

    print(f"Recording {n_frames} frames …")
    for i in range(n_frames):
        u, v = step(u, v, n=args.steps_frame)
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