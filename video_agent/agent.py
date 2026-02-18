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

import os
import logging
import textwrap
import requests
import asyncio
from typing import Optional
from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext, FunctionTool, load_artifacts
from google.adk.models import LlmRequest, LlmResponse
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from google.cloud import storage
from google.adk.artifacts import GcsArtifactService
from google.adk.sessions import VertexAiSessionService
from vertexai import agent_engines
import vertexai

# ---- Logging Setup ----
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", force=True)

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", "gs://adk_demo_staging")
GCS_ARTIFACTS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET", "gcs_artifact_svc_bucket")

vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

# --- 1. Interceptor: Save to SESSION Artifacts ---
async def intercept_to_session_callback(
    callback_context: CallbackContext, 
    llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Step 1: Intercept user message and save raw video bytes into 
    temporary SESSION artifacts. This makes the data available for tools.
    """
    if not llm_request.contents or llm_request.contents[-1].role != "user":
        return None

    last_user_message = llm_request.contents[-1]
    for i, part in enumerate(last_user_message.parts):
        if isinstance(part, dict): 
            part = types.Part.model_validate(part)

        # Check for raw video bytes (inline_data)
        if part.inline_data and part.inline_data.mime_type.startswith("video/"):
            filename = part.inline_data.display_name or f"upload_{callback_context.invocation_id}_{i}"
            
            # Canonical ADK method to save into session state
            await callback_context.save_artifact(filename=filename, artifact=part)
            logging.info("CALLBACK: Saved video to session artifacts: %s", filename)

    return None

# --- 2. Tool: Persist Session Video to Permanent GCS ---
async def save_video_to_permanent_storage_tool(
    tool_context: ToolContext, 
    video_name_in_session: str,
    final_name: Optional[str] = None
) -> str:
    """
    Step 2: Persist a video from the temporary session memory to the 
    permanent 'videos/' folder in GCS.
    """
    bucket_name = GCS_ARTIFACTS_BUCKET
    final_name = final_name or video_name_in_session
    
    try:
        # Load data from ADK Session Service
        artifact_part = await tool_context.load_artifact(video_name_in_session)
        if isinstance(artifact_part, dict): 
            artifact_part = types.Part.model_validate(artifact_part)

        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(f"videos/{final_name}")
        
        # Synchronous persistent save
        blob.upload_from_string(
            artifact_part.inline_data.data, 
            content_type=artifact_part.inline_data.mime_type
        )
        
        logging.info("TOOL: Persisted %s to permanent GCS.", final_name)
        return f"Successfully moved '{video_name_in_session}' to permanent storage as '{final_name}'."
    except Exception as e:
        return f"Error persisting video: {str(e)}"

# --- 3. Discovery Tool: Search Permanent Bucket ---
def get_all_videos_with_urls():
    """Lists blobs in 'videos/' and returns browser-authenticated URLs."""
    bucket_name = GCS_ARTIFACTS_BUCKET
    prefix = "videos/"
    try:
        storage_client = storage.Client()
        blobs = storage_client.list_blobs(bucket_name, prefix=prefix)
        video_list = []
        for blob in blobs:
            if blob.name == prefix: continue
            authenticated_url = f"https://storage.cloud.google.com/{bucket_name}/{blob.name}"
            video_list.append({"filename": blob.name.replace(prefix, ""), "url": authenticated_url})
        return video_list
    except Exception as e:
        return [f"Error: {str(e)}"]

# --- 4. Tool: Intelligent Translation sidecar ---
async def translate_video_tool(
    video_filename: str, 
    target_language: str, 
    tool_context: ToolContext
) -> str:
    """Uses Cloud Run Sidecar for emotion-aware video translation."""
    bucket_name = GCS_ARTIFACTS_BUCKET
    service_url = os.getenv('VIDEO_SERVICE_URL')
    cloud_run_url = f"{service_url}/translate-raw"
    
    try:
        storage_client = storage.Client()
        blob = storage_client.bucket(bucket_name).blob(f"videos/{video_filename}")
        if not blob.exists(): return "Error: Source video not found in GCS."

        video_bytes = blob.download_as_bytes()
        
        # --- Authentication ---
        # Get ID token for Cloud Run service
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import id_token

        auth_req = google.auth.transport.requests.Request()
        # The target audience is the base service URL (without /translate-raw)
        target_audience = service_url
        token = id_token.fetch_id_token(auth_req, target_audience)
        
        headers = {"Authorization": f"Bearer {token}"}

        response = requests.post(
            cloud_run_url,
            data={"target_language": target_language},
            files={"file": (video_filename, video_bytes, "video/mp4")},
            headers=headers,
            timeout=600
        )
        response.raise_for_status()

        output_name = f"translated_{target_language}_{video_filename}"
        storage_client.bucket(bucket_name).blob(f"videos/{output_name}").upload_from_string(response.content, content_type="video/mp4")

        # Load back into session for immediate multimodal analysis
        artifact_part = types.Part.from_bytes(data=response.content, mime_type="video/mp4")
        await tool_context.save_artifact(filename=output_name, artifact=artifact_part)

        return f"Translation successful! File saved and loaded as: {output_name}"
    except Exception as e:
        return f"Translation error: {str(e)}"

# --- Agent Definition ---
agent_instructions = textwrap.dedent("""
    You are the "Master Video Orchestrator." 
    Storage: 'gs://gcs_artifact_svc_bucket/videos/'

    CRITICAL PROTOCOLS:
    1. VISUAL SYNC (Step 0): Upon any video mention, call `load_artifacts` to see session data.
    2. PERSISTENCE: After a user uploads, use `save_video_to_permanent_storage_tool` to move it to the cloud.
    3. DISCOVERY: Use `get_all_videos_with_urls` to explore the cloud library.
    4. TRANSLATION: Use `translate_video_tool` for Sidecar-based processing.
""")

root_agent = LlmAgent(
    name="robust_video_agent",
    model="gemini-2.5-flash",
    instruction=agent_instructions,
    tools=[
        load_artifacts, # Critical for seeing session data
        FunctionTool(func=save_video_to_permanent_storage_tool),
        FunctionTool(func=get_all_videos_with_urls),
        FunctionTool(func=translate_video_tool),
    ],
    before_model_callback=intercept_to_session_callback,
)

app = agent_engines.AdkApp(
    agent=root_agent,
    artifact_service_builder=lambda: GcsArtifactService(bucket_name=GCS_ARTIFACTS_BUCKET),
    session_service_builder=lambda: VertexAiSessionService(project=PROJECT_ID, location=LOCATION),
)

# Deployment logic remains unchanged...
print("Deploying Fully-Instrumented Video Agent to Vertex AI...")

RESOURCE_NAME = os.getenv("VIDEO_AGENT_RESOURCE_NAME")
VIDEO_SERVICE_URL = os.getenv("VIDEO_SERVICE_URL")
SERVICE_ACCOUNT = os.getenv("SERVICE_ACCOUNT")

deployment_config = {
    "agent_engine": app,
    "display_name": "callback_video_agent_v1",
    "requirements": [
        "cloudpickle==3.0",
        "pydantic==2.12.5",
        "google-adk>=1.22.1",
        "google-cloud-aiplatform[adk,agent-engines]>=1.135.0",
        "google-cloud-storage>=3.8.0",
        "google-cloud-logging>=3.11.0",
        "google-genai>=1.60.0",
        "requests>=2.32.5",
        "google-auth>=2.23.0",
    ],
    "env_vars": {
        "GCS_ARTIFACTS_BUCKET": GCS_ARTIFACTS_BUCKET,
        "VIDEO_SERVICE_URL": VIDEO_SERVICE_URL,
    },
    "min_instances": 2,
    "max_instances": 10,
}

if SERVICE_ACCOUNT:
    deployment_config["service_account"] = SERVICE_ACCOUNT

if RESOURCE_NAME:
    print(f"Updating existing reasoning engine: {RESOURCE_NAME}")
    remote_agent = agent_engines.update(resource_name=RESOURCE_NAME, **deployment_config)
else:
    print("Creating new reasoning engine...")
    remote_agent = agent_engines.create(**deployment_config)

print(f"Update complete! Resource: {remote_agent.resource_name}")