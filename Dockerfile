# slim keeps the image small; ffmpeg is not available as a pip package so we need the OS layer
FROM python:3.11-slim

# ffmpeg is required by turing_mitosis_mpeg.py to encode raw RGB frames into H.264 MP4 via stdin pipe
# --no-install-recommends and cache cleanup keep the image lean
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements separately so Docker can cache the pip layer — only re-runs when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project after dependencies to maximise layer cache hits
COPY . .

# Ensure the output directory exists; Flask writes MP4s here and serves them for download
RUN mkdir -p generated_files

# Flask default port
EXPOSE 5000

# web/app.py sets threaded=True so concurrent status-poll requests don't block the simulation thread
CMD ["python", "web/app.py"]
