# Use Python 3.11
FROM python:3.11-slim

# 1. Environment Variables
# PYTHONUNBUFFERED=1 ensures logs stream to Cloud Console immediately
# CUDA_VISIBLE_DEVICES="" forces Torch to use CPU and ignore GPU checks
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=""

# 2. Install System Dependencies
# ffmpeg: required for audio/video processing
# gcc & python3-dev: required to compile AI library dependencies (like Demucs/Torch)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. Work Directory
WORKDIR /app

# 4. Install Python Dependencies
# We copy this first to leverage Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy Application Code
COPY main.py .

# 6. Start Service
# Keep-alive timeout increased for long AI processing tasks
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "300"]