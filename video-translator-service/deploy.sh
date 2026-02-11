#!/bin/bash
set -e

# --- 1. Configuration ---
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
SERVICE_NAME="video-translator"
# Stable bucket name
BUCKET_NAME="${PROJECT_ID}-video-translation-storage"

echo "------------------------------------------------"
echo "🚀 Starting High-Capacity AI Deployment (24Gi)"
echo "Project: ${PROJECT_ID} | Region: ${REGION}"
echo "------------------------------------------------"

# --- 2. Enable APIs ---
echo "✅ Enabling necessary APIs..."
gcloud services enable \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    speech.googleapis.com \
    translate.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com

# --- 3. Manage Storage Bucket ---
if ! gcloud storage buckets describe gs://${BUCKET_NAME} &>/dev/null; then
  echo "📦 Creating storage bucket: gs://${BUCKET_NAME}"
  gcloud storage buckets create gs://${BUCKET_NAME} --location=${REGION}
else
  echo "📦 Storage bucket already exists."
fi

# --- 4. Build and Deploy to Cloud Run ---
echo "🏗️ Building and Deploying to Cloud Run..."
# --execution-environment=gen2: Required for FFmpeg and Demucs
# --memory 24Gi: High capacity for AI stem separation and long videos
# --cpu 8: Required to support the 24Gi memory allocation
# --concurrency 1: Ensures 100% of resources are dedicated to one video at a time
# --timeout 3600: 1 hour limit
gcloud run deploy ${SERVICE_NAME} \
    --source . \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --execution-environment=gen2 \
    --memory 24Gi \
    --cpu 8 \
    --concurrency 1 \
    --timeout 3600 \
    --no-cpu-throttling \
    --cpu-boost \
    --set-env-vars BUCKET_NAME=${BUCKET_NAME},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}

# --- 5. Final Status ---
URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)')
echo "------------------------------------------------"
echo "✨ Deployment Success!"
echo "Service URL: ${URL}"
echo "Memory Allocated: 24Gi | CPUs: 8"
echo "------------------------------------------------"