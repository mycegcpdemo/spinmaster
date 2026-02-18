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
#
# ==========================================
# CONFIGURATION - EDIT THESE VALUES
# This script helps you register an agent running on Agent Engine (AE) with Gemini Enterprise
# It registers the agent automatically by creating and running the curls commands.
# It is based on the docs: https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent#register_adk_agent-drest
# Date 2nd Feb 2026
# =========================================
# Ensure you have jq install on your system to parse JSON:
# For Mac: brew install jq
# 
# Ensure your credential.json file is in the same folder as this script
# ==========================================

# 1. Path to your downloaded OAuth JSON
CREDENTIALS_FILE="credentials.json"

# 2. Google Cloud Project Config
PROJECT_ID="your-project-id"
LOCATION="global"                # global, us, or eu
ENDPOINT_LOCATION="global"       # global, us, or eu (prefix for API)

# 3. Agent & App Config
APP_ID="your-app-id"             # The Gemini Enterprise App ID
AUTH_ID="my-agent-auth-01"       # Arbitrary ID for the Auth Resource
AGENT_DISPLAY_NAME="My BigQuery Agent"
AGENT_DESCRIPTION="An agent that queries BigQuery on behalf of the user."

# 4. Reasoning Engine (The deployed code)
REASONING_ENGINE_ID="your-reasoning-engine-id" # Only the number ID of the deployed reasoning engine
REASONING_ENGINE_LOCATION="us-central1" # Where the engine is deployed

# 5. OAuth Scopes (Space separated), im using cloud-platform for broad access
# Example: platform wide scope.
SCOPES='https://www.googleapis.com/auth/cloud-platform'

# ==========================================
# LOGIC - DO NOT EDIT BELOW
# ==========================================

# Check for jq
if ! command -v jq &> /dev/null; then
    echo "Error: 'jq' is not installed. Please install it to parse the JSON file."
    exit 1
fi

echo "--- Reading credentials.json ---"
CLIENT_ID=$(jq -r '.web.client_id // empty' "$CREDENTIALS_FILE")
CLIENT_SECRET=$(jq -r '.web.client_secret // empty' "$CREDENTIALS_FILE")
TOKEN_URI=$(jq -r '.web.token_uri // "https://oauth2.googleapis.com/token"' "$CREDENTIALS_FILE")

if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "Error: Could not extract client_id or client_secret from $CREDENTIALS_FILE"
    exit 1
fi

echo "--- Fetching Project Number ---"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
if [[ -z "$PROJECT_NUMBER" ]]; then
    echo "Error: Could not determine Project Number. Check gcloud auth."
    exit 1
fi

echo "--- Constructing Authorization URI ---"
# URL Encode the Redirect URI and Scopes
REDIRECT_URI="https://vertexaisearch.cloud.google.com/static/oauth/oauth.html"
ENCODED_REDIRECT=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$REDIRECT_URI'))")
ENCODED_SCOPES=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SCOPES'))")

# Construct the full Auth URI required by the API
FULL_AUTH_URI="https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=${ENCODED_REDIRECT}&scope=${ENCODED_SCOPES}&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"

echo "--------------------------------------------------------"
echo "STEP 1: Creating Authorization Resource ($AUTH_ID)"
echo "--------------------------------------------------------"

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
echo ""
echo "--------------------------------------------------------"
echo "STEP 2: Registering ADK Agent"
echo "--------------------------------------------------------"

curl -X POST \
   -H "Authorization: Bearer $(gcloud auth print-access-token)" \
   -H "Content-Type: application/json" \
   -H "X-Goog-User-Project: ${PROJECT_ID}" \
   "https://${ENDPOINT_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection/engines/${APP_ID}/assistants/default_assistant/agents" \
   -d "{
      \"displayName\": \"${AGENT_DISPLAY_NAME}\",
      \"description\": \"${AGENT_DESCRIPTION}\",
      \"adk_agent_definition\": {
         \"provisioned_reasoning_engine\": {
            \"reasoning_engine\": \"projects/${PROJECT_ID}/locations/${REASONING_ENGINE_LOCATION}/reasoningEngines/${REASONING_ENGINE_ID}\"
         }
      },
      \"authorization_config\": {
         \"tool_authorizations\": [
            \"projects/${PROJECT_NUMBER}/locations/${LOCATION}/authorizations/${AUTH_ID}\"
         ]
      }
   }"

echo ""
echo "Done."