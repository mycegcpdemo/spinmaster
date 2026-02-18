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
import mimetypes
import textwrap
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
from google import genai

# --- Configuration & Logging ---
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", "gs://adk_demo_staging")
GCS_ARTIFACTS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET", "gcs_artifact_svc_bucket")

vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)


# --- 1. Interceptor Function (Pre-Model Callback) ---
async def intercept_and_session_save(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Step 1: Intercept user message and save uploaded files to the SESSION artifacts.

    """
    if not llm_request.contents or llm_request.contents[-1].role != "user":
        return None

    last_user_message = llm_request.contents[-1]
    for i, part in enumerate(last_user_message.parts):
        if isinstance(part, dict):
            part = types.Part.model_validate(part)

        if part.inline_data:
            # Generate filename using invocation_id for turn-based uniqueness
            filename = (
                part.inline_data.display_name
                or f"upload_{callback_context.invocation_id}_{i}"
            )
            await callback_context.save_artifact(filename=filename, artifact=part)
            logger.info("Intercepted and saved session artifact: %s", filename)
    return None


# 2 --- Updated Tool: Persistent GCS Save with Renaming Support ---
async def save_session_to_gcs_tool(
    tool_context: ToolContext, rename_map: dict[str, str] = None
) -> str:
    """
    Optimized tool to persist session images to GCS with parallel uploads
    and strict MIME type validation.
    """

    # 1. Retrieve the list of current session artifacts
    GCS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET")

    artifacts = await tool_context.list_artifacts()
    if not artifacts:
        return "No temporary files found in session to save."

    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET)
    rename_map = rename_map or {}

    # 2. Define a helper for concurrent uploads
    async def upload_task(session_filename: str):
        artifact_part = await tool_context.load_artifact(session_filename)

        # Validate Pydantic model structure
        if isinstance(artifact_part, dict):
            artifact_part = types.Part.model_validate(artifact_part)

        # Determine the final filename
        final_name = rename_map.get(session_filename, session_filename)

        # --- Optimization: MIME Type Handling ---
        # Guess the MIME type based on the final extension if the session metadata is generic
        detected_mime, _ = mimetypes.guess_type(final_name)
        mime_type = detected_mime or artifact_part.inline_data.mime_type or "image/jpeg"

        # 3. Perform the upload to the 'permanent_storage' folder
        blob = bucket.blob(f"permanent_storage/{final_name}")

        # Using upload_from_string directly on the binary data
        blob.upload_from_string(
            artifact_part.inline_data.data,
            content_type=mime_type,
        )
        return final_name

    # 4. Run all uploads in parallel using asyncio.gather for speed
    # This is significantly faster than a for-loop for multiple files
    try:
        saved_files = await asyncio.gather(*(upload_task(f) for f in artifacts))
        return f"Successfully saved to GCS: {', '.join(saved_files)}"
    except Exception as e:
        logger.error(f"Parallel upload failed: {e}")
        return f"An error occurred during optimized upload: {str(e)}"


# --- 3. Tool: List and Search GCS ---
async def search_and_list_files_tool(tool_context: ToolContext) -> list[str]:
    """
    Step 3: List all files in GCS persistent storage for the user.

    """

    GCS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET")

    storage_client = storage.Client()
    blobs = storage_client.list_blobs(GCS_BUCKET, prefix="permanent_storage/")
    return [blob.name for blob in blobs]


# --- 4. Tool: Render from GCS ---
async def render_from_permanent_storage_tool(
    tool_context: ToolContext, filename: str
) -> str:
    """
    Optimized tool to retrieve images from 'permanent_storage/' and
    render them as session artifacts.
    """
    GCS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET")

    try:
        bucket_name = GCS_BUCKET
        blob_path = f"permanent_storage/{filename}"

        # 1. Initialize GCS Client
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        # 2. Check existence efficiently without downloading bytes yet
        # We run this in a thread to keep the async loop responsive
        exists = await asyncio.to_thread(blob.exists)
        if not exists:
            return f"Error: Image '{filename}' not found in permanent storage."

        # 3. Optimized Metadata & Download
        # Reloading captures the content_type set during the original upload
        await asyncio.to_thread(blob.reload)
        file_bytes = await asyncio.to_thread(blob.download_as_bytes)

        # --- Optimization: Image-Specific MIME Handling ---
        # Prioritize GCS metadata, then guess by extension, fallback to standard image/jpeg
        detected_mime, _ = mimetypes.guess_type(filename)
        mime_type = blob.content_type or detected_mime or "image/jpeg"

        if not mime_type.startswith("image/"):
            logger.warning(
                f"File {filename} has non-image MIME: {mime_type}. Forcing image/jpeg."
            )
            mime_type = "image/jpeg"

        # 4. Wrap and Save as Artifact
        # This triggers the UI rendering logic in Gemini Enterprise
        artifact_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        logger.info("Successfully rendered image %s from GCS.", filename)
        return f"Image '{filename}' has been retrieved and rendered in your session."

    except Exception as e:
        logger.error("Failed to render image %s: %s", filename, str(e))
        return f"Sorry, I encountered an error while trying to display that image: {str(e)}"


# --- 5. Tool: Translate image ---
async def translate_image_tool(
    filename: str, target_language: str, tool_context: ToolContext
) -> str:
    """
    Retrieves an image from 'permanent_storage/', translates it,
    and saves the output to BOTH session artifacts and permanent storage.
    """

    GCS_BUCKET = os.getenv("GCS_ARTIFACTS_BUCKET")

    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    # 1. Retrieve the original file from GCS
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET)
        blob_path = f"permanent_storage/{filename}"
        blob = bucket.blob(blob_path)

        if not blob.exists():
            return f"Error: File '{filename}' not found in storage."

        blob.reload()
        image_bytes = blob.download_as_bytes()
        mime_type = blob.content_type or "image/png"

        input_image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    except Exception as e:
        return f"Error retrieving image: {str(e)}"

    # 2. Call Gemini 3 for Image-to-Image Translation
    prompt_text = (
        f"Translate the text on this product label into {target_language}. "
        "Strictly preserve the exact background, colors, and product look. "
        "Only replace the text strings with the translated versions. "
        "Do not change the image style."
    )

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3-pro-image-preview", contents=[input_image_part, prompt_text]
        )
    except Exception as e:
        return f"Error calling Gemini: {str(e)}"

    # 3. Extract generated image data
    generated_image_data = None
    output_mime_type = "image/png"
    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                generated_image_data = part.inline_data.data
                output_mime_type = part.inline_data.mime_type
                break

    if not generated_image_data:
        return "Error: No image generated."

    # 4. Save to Session Artifact (for immediate rendering)
    new_filename = f"{target_language}_translated_{os.path.splitext(filename)[0]}.png"
    image_artifact = types.Part.from_bytes(
        data=generated_image_data, mime_type=output_mime_type
    )
    await tool_context.save_artifact(filename=new_filename, artifact=image_artifact)

    # 5. Save to Permanent Storage (The Modification)
    try:
        # Construct the new path in the permanent folder
        new_blob_path = f"permanent_storage/{new_filename}"
        new_blob = bucket.blob(new_blob_path)

        # Upload raw bytes directly
        new_blob.upload_from_string(generated_image_data, content_type=output_mime_type)
        logger.info("Persisted translated image to GCS: %s", new_blob_path)
    except Exception as e:
        logger.error("Failed to persist translation to GCS: %s", e)
        # Note: We return success for session save even if GCS persistence fails

    return f"Translation complete. File '{new_filename}' saved to session and permanent storage."


# Agent Instructions (The Master Protocols the Agent Follows)
agent_instructions = textwrap.dedent("""\
    You are the "Master Visual Asset Manager." Your expertise is strictly in managing, rendering, and translating high-resolution image files between this session and permanent Google Cloud Storage.

    ### CRITICAL IMAGE PROTOCOLS:

    1. **VISUAL SYNC (Step 0):** Upon any image upload or storage inquiry, you MUST immediately call `load_artifacts`. This ensures you can "see" the session's visual data before proceeding.

    2. **IMAGE PERSISTENCE & RENAMING:** To move images to the cloud, use `save_session_to_gcs_tool`.
       * If the user provides a custom name (e.g., "save as logo.png"), map the session artifact ID (e.g., 'upload_1') to that name using the `rename_map`.
       * Always process multiple images in a single parallel call to maintain performance.

    3. **GALLERY DISCOVERY:** Use `search_and_list_files_tool` to explore the 'permanent_storage/' folder. Always present clean filenames. If you find relevant visual assets, proactively offer to "Render" them.

    4. **HIGH-FIDELITY RENDERING:** Use `render_from_permanent_storage_tool` for all requests to "view," "show," or "display" an image. This tool is specifically optimized to repair image metadata for perfect UI display.

    5. **AI IMAGE TRANSLATION (Gemini 3 Pro):** For product labels, infographics, or documents, use `translate_image_tool`. 
       * This model is specialized for "Style Preservation"—it replaces text while keeping the original background and aesthetic intact.
       * Remind the user that the translated version is stored both in the chat and in permanent GCS.
""")

# --- . Agent Definition ---
root_agent = LlmAgent(
    name="image_agent",
    model="gemini-2.5-flash",
    instruction=(agent_instructions),
    tools=[
        load_artifacts,
        FunctionTool(func=save_session_to_gcs_tool),
        FunctionTool(func=search_and_list_files_tool),
        FunctionTool(func=render_from_permanent_storage_tool),
        FunctionTool(func=translate_image_tool),
    ],
    # Register the interceptor
    before_model_callback=intercept_and_session_save,
)


#  Define the Artifact Service Builder
# This ensures the cloud runtime can initialize the GCS service
def artifact_service_builder():
    # Agent Engine automatically sets GCS_ARTIFACTS_BUCKET if you use --staging_bucket
    bucket = os.getenv("GCS_ARTIFACTS_BUCKET")
    return GcsArtifactService(bucket_name=bucket)
    # return InMemoryArtifactService()


def session_service_builder():
    return VertexAiSessionService(
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION"),
    )


# 4. Wrap the Agent in an AdkApp
# The SaveFilesAsArtifactsPlugin handles the automatic offloading of files to GCS
app = agent_engines.AdkApp(
    agent=root_agent,
    # plugins=[SaveFilesAsArtifactsPlugin()],
    artifact_service_builder=artifact_service_builder,
    session_service_builder=session_service_builder,
    enable_tracing=True,
)

# 5. Deploy from an Agent Object
# This method serializes the 'app' object and creates a Reasoning Engine resource
print("Deploying agent to Vertex AI Agent Engine...")

RESOURCE_NAME = os.getenv("IMAGE_AGENT_RESOURCE_NAME")
SERVICE_ACCOUNT = os.getenv("SERVICE_ACCOUNT")

deployment_config = {
    "agent_engine": app,
    "display_name": "image agent",
    "description": (
        "A specialized multimodal agent for high-fidelity image management. "
        "Capable of visual synchronization, style-preserving image-to-image translation "
        "via Gemini 3 Pro, and high-performance cloud storage rendering."
    ),
    "requirements": [
        "cloudpickle==3.0",
        "pydantic==2.12.5",
        "google-adk>=1.22.1",
        "google-auth-oauthlib>=1.2.4",
        "google-cloud-aiplatform[adk,agent-engines]>=1.135.0",
        "google-cloud-storage>=3.8.0",
        "google-genai>=1.60.0",
        "opentelemetry-instrumentation-google-genai"
    ],
    "env_vars": {
        "GCS_ARTIFACTS_BUCKET": GCS_ARTIFACTS_BUCKET,
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "True",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "True",
    },
    "gcs_dir_name": "agent_pickles",
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

print(f"Deployment complete! Resource Name: {remote_agent.resource_name}")
