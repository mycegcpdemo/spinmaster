# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


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