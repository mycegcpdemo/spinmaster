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

# set -e # Don't exit on error so we can attempt to clean up everything

# ==============================================================================
# PROJECT CONFIGURATION
# ==============================================================================
PROJECT_ID=""             # Google Cloud Project ID
REGION=""      # Region for Cloud Run and Vertex AI
GCS_BUCKET=""             # Bucket for agent artifacts
STAGING_BUCKET=""         # Staging bucket for agent deployment

# ==============================================================================
# GEMINI ENTERPRISE APP DETAILS
# ==============================================================================
GEMINI_APP_ID=""          # The Gemini Enterprise App ID (Engine ID)

# (OPTIONAL) SPECIFIC RESOURCE IDS - If known, enter them here to ensure deletion
# If left empty, the script will attempt to find or use default names.
IMAGE_AGENT_ID=""
VIDEO_AGENT_ID=""
AUTH_ID_IMAGE=""
AUTH_ID_VIDEO=""

# SERVICE ACCOUNTS
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
        eval "$VAR_NAME="$NEW_VAL""
    fi
}

echo "------------------------------------------------"
echo "🗑️ Starting Full SpinMaster Rollback"
echo "------------------------------------------------"

check_variable "PROJECT_ID" "$PROJECT_ID" "Enter your Google Cloud Project ID"
check_variable "GEMINI_APP_ID" "$GEMINI_APP_ID" "Enter your Gemini Enterprise App ID"

gcloud config set project ${PROJECT_ID}

# ==============================================================================
# 1. UNREGISTER AGENTS FROM GEMINI ENTERPRISE
# ==============================================================================
echo "🔗 Unregistering agents from Gemini Enterprise..."

# Note: Finding specific agent IDs in Discovery Engine via CLI is complex.
# This script assumes you may need to delete them manually if IDs aren't known,
# but we will attempt to list and delete agents that match our naming convention.

# This is a helper to list and delete agents if possible, but Discovery Engine API 
# often requires specific IDs. For safety, we recommend checking the UI.
echo "⚠️  Note: Automated deletion of Discovery Engine agents/auths requires specific IDs."
echo "Please ensure you manually remove any 'SpinMaster' agents in the Gemini Enterprise UI if this step fails."

if [[ -n "$AUTH_ID_IMAGE" ]]; then
    echo "Deleting Authorization: ${AUTH_ID_IMAGE}"
    curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" 
      "https://global-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/authorizations/${AUTH_ID_IMAGE}"
fi

if [[ -n "$AUTH_ID_VIDEO" ]]; then
    echo "Deleting Authorization: ${AUTH_ID_VIDEO}"
    curl -X DELETE -H "Authorization: Bearer $(gcloud auth print-access-token)" 
      "https://global-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/authorizations/${AUTH_ID_VIDEO}"
fi

# ==============================================================================
# 2. DELETE VERTEX AI REASONING ENGINES
# ==============================================================================
echo "🤖 Deleting Vertex AI Reasoning Engines..."

delete_engine() {
    local ENGINE_ID=$1
    if [[ -n "$ENGINE_ID" ]]; then
        echo "Deleting Reasoning Engine: ${ENGINE_ID}"
        gcloud ai reasoning-engines delete "${ENGINE_ID}" --location="${REGION}" --quiet || echo "Failed to delete engine ${ENGINE_ID}"
    else
        echo "No Engine ID provided for deletion. Searching for agents with 'agent' in display name..."
        # Optional: Attempt to find by display name
        gcloud ai reasoning-engines list --location="${REGION}" --format="value(name)" --filter="display_name:agent" | xargs -I {} gcloud ai reasoning-engines delete {} --location="${REGION}" --quiet
    fi
}

delete_engine "$IMAGE_AGENT_ID"
delete_engine "$VIDEO_AGENT_ID"

# ==============================================================================
# 3. DELETE CLOUD RUN SERVICE
# ==============================================================================
echo "🏗️ Deleting Cloud Run Service: video-translator..."
gcloud run services delete video-translator --region="${REGION}" --quiet || echo "Cloud Run service not found."

# ==============================================================================
# 4. DELETE SERVICE ACCOUNTS & IAM BINDINGS
# ==============================================================================
echo "👤 Deleting Service Accounts..."

RUN_SA_EMAIL="${RUN_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
AGENT_SA_EMAIL="${AGENT_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts delete "${RUN_SA_EMAIL}" --quiet || echo "SA ${RUN_SA_EMAIL} not found."
gcloud iam service-accounts delete "${AGENT_SA_EMAIL}" --quiet || echo "SA ${AGENT_SA_EMAIL} not found."

# ==============================================================================
# 5. DELETE GCS BUCKETS (OPTIONAL)
# ==============================================================================
read -p "❓ Do you want to delete the GCS buckets and ALL their contents? (y/N): " DELETE_BUCKETS
if [[ "$DELETE_BUCKETS" =~ ^[Yy]$ ]]; then
    check_variable "GCS_BUCKET" "$GCS_BUCKET" "Enter GCS Bucket name to delete"
    check_variable "STAGING_BUCKET" "$STAGING_BUCKET" "Enter Staging Bucket to delete (gs://...)"
    
    echo "📦 Deleting buckets..."
    gcloud storage rm -r "${GCS_BUCKET}" || echo "Bucket content removal failed."
    gcloud storage buckets delete "${GCS_BUCKET}" --quiet || echo "Failed to delete bucket."
    
    gcloud storage rm -r "${STAGING_BUCKET}" || echo "Staging bucket content removal failed."
    gcloud storage buckets delete "${STAGING_BUCKET}" --quiet || echo "Failed to delete staging bucket."
fi

echo "------------------------------------------------"
echo "✨ Rollback process completed!"
echo "Check the Google Cloud Console to ensure all resources are removed."
echo "------------------------------------------------"
