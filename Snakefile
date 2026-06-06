configfile: "config.yaml"


rule all:
    input:
        expand("generated_files/{name}.mp4", name=config["runs"].keys())


rule simulate:
    output:
        "generated_files/{name}.mp4"
    params:
        p=lambda wc: config["runs"][wc.name]
    run:
        import subprocess, sys
        p = params.p
        cmd = [
            sys.executable, "turing_mitosis_mpeg.py",
            "--seeds",       str(p["seeds"]),
            "--fps",         str(p["fps"]),
            "--duration",    str(p["duration"]),
            "--width",       str(p["width"]),
            "--height",      str(p["height"]),
            "--burn_in",     str(p["burn_in"]),
            "--steps_frame", str(p["steps_frame"]),
            "--crf",         str(p["crf"]),
            "--out",         str(output),
        ]
        subprocess.run(cmd, check=True)
