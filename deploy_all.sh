#!/bin/bash

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

set -e

# ==============================================================================
# PROJECT CONFIGURATION
# ==============================================================================
PROJECT_ID=""             # Google Cloud Project ID
REGION=""      # Region for Cloud Run and Vertex AI
GCS_BUCKET="gs://bucket_name"             # Bucket for agent artifacts (e.g., gs://my-artifacts)
STAGING_BUCKET="gs://bucket_name"         # Staging bucket for agent deployment (e.g., gs://my-staging)

# ==============================================================================
# GEMINI ENTERPRISE APP DETAILS
# ==============================================================================
GEMINI_APP_ID=""          # The Gemini Enterprise App ID (also known as Engine ID)
LOCATION=""         # global, us, or eu
ENDPOINT_LOCATION="" # global, us, or eu (prefix for API)

# OAUTH CONFIGURATION
# Ensure your credentials.json is in 'add_agent_scripts/' folder
CREDENTIALS_FILE="add_agent_scripts/credentials.json"
AUTH_ID_IMAGE="image-agent-auth-$(date +%s)"
AUTH_ID_VIDEO="video-agent-auth-$(date +%s)"
SCOPES='https://www.googleapis.com/auth/cloud-platform'

# (OPTIONAL) RESOURCE NAMES - Set these if you want to update existing agents
# IMAGE_AGENT_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."
# VIDEO_AGENT_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."

# (OPTIONAL) SERVICE ACCOUNTS - Names for the dedicated service accounts
RUN_SA_NAME="video-translator-sa"
AGENT_SA_NAME="spinmaster-agent-sa"

# ==============================================================================
# 0. VALIDATE & PROMPT FOR MISSING VALUES
# ==============================================================================
check_variable() {
    local VAR_NAME=$1
    local VAR_VAL=$2
    local PROMPT_TEXT=$3
    if [[ -z "$VAR_VAL" ]]; then
        read -p "❓ $PROMPT_TEXT: " NEW_VAL
        eval "$VAR_NAME=\"$NEW_VAL\""
    fi
}

check_variable "PROJECT_ID" "$PROJECT_ID" "Enter your Google Cloud Project ID"
check_variable "GCS_BUCKET" "$GCS_BUCKET" "Enter GCS Bucket name for artifacts (gs://...)"
check_variable "STAGING_BUCKET" "$STAGING_BUCKET" "Enter Staging Bucket (gs://...)"
check_variable "GEMINI_APP_ID" "$GEMINI_APP_ID" "Enter your Gemini Enterprise App ID"

# Ensure buckets have gs:// prefix
if [[ ! "$GCS_BUCKET" =~ ^gs:// ]]; then GCS_BUCKET="gs://${GCS_BUCKET}"; fi
if [[ ! "$STAGING_BUCKET" =~ ^gs:// ]]; then STAGING_BUCKET="gs://${STAGING_BUCKET}"; fi

# Extract name without gs:// for commands that require it
GCS_BUCKET_NAME=$(echo "$GCS_BUCKET" | sed 's/gs:\/\///')

# Validate credentials.json
if [ ! -f "$CREDENTIALS_FILE" ]; then
    echo "❌ Error: $CREDENTIALS_FILE not found. Please place your OAuth credentials.json in add_agent_scripts/."
    exit 1
fi

# ==============================================================================
# 1. SETUP, ENABLE APIS & IAM
# ==============================================================================
echo "------------------------------------------------"
echo "🚀 Starting Full SpinMaster Deployment (Least Privilege)"
echo "Project: ${PROJECT_ID} | Region: ${REGION}"
echo "------------------------------------------------"

gcloud config set project ${PROJECT_ID}

echo "✅ Enabling necessary APIs..."
gcloud services enable \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    speech.googleapis.com \
    translate.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    discoveryengine.googleapis.com \
    iam.googleapis.com

# --- Create Buckets if they don't exist ---
if ! gcloud storage buckets describe ${GCS_BUCKET} &>/dev/null; then
  echo "📦 Creating storage bucket: ${GCS_BUCKET}"
  gcloud storage buckets create ${GCS_BUCKET} --location=${REGION}
else
  echo "📦 Storage bucket ${GCS_BUCKET} already exists."
fi

if ! gcloud storage buckets describe ${STAGING_BUCKET} &>/dev/null; then
  echo "📦 Creating staging bucket: ${STAGING_BUCKET}"
  gcloud storage buckets create ${STAGING_BUCKET} --location=${REGION}
else
  echo "📦 Staging bucket ${STAGING_BUCKET} already exists."
fi

# --- 1.1 Create Service Accounts ---
create_sa_if_not_exists() {
    local SA_NAME=$1
    local SA_DISPLAY=$2
    if ! gcloud iam service-accounts describe ${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com &>/dev/null; then
        echo "👤 Creating service account: ${SA_NAME}"
        gcloud iam service-accounts create ${SA_NAME} --display-name="${SA_DISPLAY}"
    fi
}

create_sa_if_not_exists "${RUN_SA_NAME}" "Cloud Run Video Translator Service Account"
create_sa_if_not_exists "${AGENT_SA_NAME}" "SpinMaster ADK Agent Service Account"

RUN_SA_EMAIL="${RUN_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA_EMAIL="${AGENT_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# --- 1.2 Assign IAM Roles (Least Privilege) ---
echo "🔐 Assigning IAM Roles..."

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Build Identities Permissions (Needed for Cloud Run --source and Agent Engine builds)
echo "🛠️  Authorizing build identities..."
for SA in "$CLOUDBUILD_SA" "$COMPUTE_SA"; do
    gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${SA}" --role="roles/artifactregistry.admin" --quiet > /dev/null
    gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${SA}" --role="roles/storage.admin" --quiet > /dev/null
done

# Cloud Run SA Roles
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/storage.objectAdmin" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/speech.client" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/cloudtranslate.user" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/logging.logWriter" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/aiplatform.user" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${RUN_SA_EMAIL}" --role="roles/serviceusage.serviceUsageConsumer" --quiet > /dev/null

# Agent SA Roles
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${AGENT_SA_EMAIL}" --role="roles/storage.objectAdmin" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${AGENT_SA_EMAIL}" --role="roles/aiplatform.user" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${AGENT_SA_EMAIL}" --role="roles/logging.logWriter" --quiet > /dev/null
gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:${AGENT_SA_EMAIL}" --role="roles/serviceusage.serviceUsageConsumer" --quiet > /dev/null

# ==============================================================================
# 2. DEPLOY CLOUD RUN SERVICE
# ==============================================================================
echo "🏗️ Deploying Video Translator Service to Cloud Run..."
cd video-translator-service
SERVICE_NAME="video-translator"
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
    --service-account ${RUN_SA_EMAIL} \
    --set-env-vars BUCKET_NAME=${GCS_BUCKET_NAME},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}

CLOUD_RUN_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)')
cd ..

# Assign Run Invoker to Agent SA
gcloud run services add-iam-policy-binding ${SERVICE_NAME} \
    --region=${REGION} \
    --member="serviceAccount:${AGENT_SA_EMAIL}" \
    --role="roles/run.invoker" --quiet > /dev/null

echo "⏳ Waiting 30 seconds for IAM propagation..."
sleep 30

echo "✅ Cloud Run Service deployed at: ${CLOUD_RUN_URL}"

# ==============================================================================
# 3. DEPLOY IMAGE AGENT (VERTEX AI REASONING ENGINE)
# ==============================================================================
echo "🤖 Deploying Image Agent to Vertex AI Agent Engine..."
export GOOGLE_CLOUD_PROJECT=${PROJECT_ID}
export GOOGLE_CLOUD_LOCATION=${REGION}
export STAGING_BUCKET=${STAGING_BUCKET}
export GCS_ARTIFACTS_BUCKET=${GCS_BUCKET_NAME}
export IMAGE_AGENT_RESOURCE_NAME=${IMAGE_AGENT_RESOURCE_NAME}
export SERVICE_ACCOUNT=${AGENT_SA_EMAIL}

IMAGE_AGENT_OUTPUT=$(python3 image_Agent/agent.py)
echo "$IMAGE_AGENT_OUTPUT"
IMAGE_AGENT_ID=$(echo "$IMAGE_AGENT_OUTPUT" | grep "Resource Name:" | sed 's/.*reasoningEngines\///')

if [[ -z "$IMAGE_AGENT_ID" ]]; then
  echo "❌ Failed to retrieve Image Agent ID."
  exit 1
fi
echo "✅ Image Agent deployed with ID: ${IMAGE_AGENT_ID}"

# ==============================================================================
# 4. DEPLOY VIDEO AGENT (VERTEX AI REASONING ENGINE)
# ==============================================================================
echo "🤖 Deploying Video Agent to Vertex AI Agent Engine..."
export VIDEO_SERVICE_URL=${CLOUD_RUN_URL}
export VIDEO_AGENT_RESOURCE_NAME=${VIDEO_AGENT_RESOURCE_NAME}

VIDEO_AGENT_OUTPUT=$(python3 video_agent/agent.py)
echo "$VIDEO_AGENT_OUTPUT"
VIDEO_AGENT_ID=$(echo "$VIDEO_AGENT_OUTPUT" | grep "Resource:" | sed 's/.*reasoningEngines\///')

if [[ -z "$VIDEO_AGENT_ID" ]]; then
  echo "❌ Failed to retrieve Video Agent ID."
  exit 1
fi
echo "✅ Video Agent deployed with ID: ${VIDEO_AGENT_ID}"

# ==============================================================================
# 5. REGISTER AGENTS WITH GEMINI ENTERPRISE
# ==============================================================================
echo "🔗 Registering agents with Gemini Enterprise..."

# Helper for Registration
register_agent() {
    local AGENT_NAME=$1
    local AGENT_DESC=$2
    local ENGINE_ID=$3
    local AUTH_ID=$4

    echo "--- Registering ${AGENT_NAME} ---"
    
    # 1. Create Authorization Resource
    CLIENT_ID=$(jq -r '.web.client_id // empty' "$CREDENTIALS_FILE")
    CLIENT_SECRET=$(jq -r '.web.client_secret // empty' "$CREDENTIALS_FILE")
    TOKEN_URI=$(jq -r '.web.token_uri // "https://oauth2.googleapis.com/token"' "$CREDENTIALS_FILE")
    REDIRECT_URI="https://vertexaisearch.cloud.google.com/static/oauth/oauth.html"
    ENCODED_REDIRECT=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$REDIRECT_URI'))")
    ENCODED_SCOPES=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SCOPES'))")
    FULL_AUTH_URI="https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=${ENCODED_REDIRECT}&scope=${ENCODED_SCOPES}&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"

    curl -X POST \
      -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      -H "Content-Type: application/json" \
      -H "X-Goog-User-Project: ${PROJECT_ID}" \
      "https://${ENDPOINT_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${LOCATION}/authorizations?authorizationId=${AUTH_ID}" \
      -d "{
      \"name\": \"projects/${PROJECT_ID}/locations/${LOCATION}/authorizations/${AUTH_ID}\",
      \"serverSideOauth2\": {
          \"clientId\": \"${CLIENT_ID}\",
          \"clientSecret\": \"${CLIENT_SECRET}\",
          \"authorizationUri\": \"${FULL_AUTH_URI}\",
          \"tokenUri\": \"${TOKEN_URI}\"
        }
      }"

    echo ""
    # 2. Register ADK Agent
    PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

    curl -X POST \
       -H "Authorization: Bearer $(gcloud auth print-access-token)" \
       -H "Content-Type: application/json" \
       -H "X-Goog-User-Project: ${PROJECT_ID}" \
       "https://${ENDPOINT_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection/engines/${GEMINI_APP_ID}/assistants/default_assistant/agents" \
       -d "{
          \"displayName\": \"${AGENT_NAME}\",
          \"description\": \"${AGENT_DESC}\",
          \"adk_agent_definition\": {
             \"provisioned_reasoning_engine\": {
                \"reasoning_engine\": \"projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}\"
             }
          },
          \"authorization_config\": {
             \"tool_authorizations\": [
                \"projects/${PROJECT_NUMBER}/locations/${LOCATION}/authorizations/${AUTH_ID}\"
             ]
          }
       }"
    echo ""
}

# Register Image Agent
register_agent "SpinMaster Image Agent" "Manages and translates high-fidelity images." "$IMAGE_AGENT_ID" "$AUTH_ID_IMAGE"

# Register Video Agent
register_agent "SpinMaster Video Agent" "Orchestrates emotion-aware video translation." "$VIDEO_AGENT_ID" "$AUTH_ID_VIDEO"

echo "------------------------------------------------"
echo "✨ All components deployed and registered!"
echo "Cloud Run URL: ${CLOUD_RUN_URL}"
echo "Image Agent ID: ${IMAGE_AGENT_ID}"
echo "Video Agent ID: ${VIDEO_AGENT_ID}"
echo "------------------------------------------------"
