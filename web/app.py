from flask import Flask, request, jsonify, render_template, send_file
import subprocess
import threading
import uuid
import os
import sys

PROJECT_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated_files")
os.makedirs(GENERATED_DIR, exist_ok=True)

app  = Flask(__name__)
jobs = {}


def run_simulation(job_id: str, args: dict):
    output_path = os.path.join(GENERATED_DIR, f"{job_id}.mp4")
    cmd = [
        sys.executable, "turing_mitosis_mpeg.py",
        "--out",         output_path,
        "--seeds",       str(args["seeds"]),
        "--fps",         str(args["fps"]),
        "--duration",    str(args["duration"]),
        "--width",       str(args["width"]),
        "--height",      str(args["height"]),
        "--burn_in",     str(args["burn_in"]),
        "--steps_frame", str(args["steps_frame"]),
        "--crf",         str(args["crf"]),
        "--cell_size",   str(args.get("cell_size", 1)),
        "--Du",          str(args.get("Du", 0.16)),
        "--Dv",          str(args.get("Dv", 0.08)),
        "--F",           str(args.get("F", 0.0367)),
        "--K",           str(args.get("K", 0.0649)),
    ]
    if args.get("seed") is not None:
        cmd += ["--seed", str(args["seed"])]

    jobs[job_id]["status"] = "running"
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode == 0:
        jobs[job_id]["status"] = "done"
        jobs[job_id]["output"] = output_path
    else:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"]  = result.stderr[-2000:]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run():
    args   = request.get_json()
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"status": "queued"}
    threading.Thread(target=run_simulation, args=(job_id, args), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({k: v for k, v in job.items() if k != "output"})


@app.route("/view/<job_id>")
def view(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "not ready"}), 404
    return send_file(job["output"], mimetype="video/mp4")


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "not ready"}), 404
    return send_file(job["output"], as_attachment=True, download_name=f"mitosis_{job_id}.mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
