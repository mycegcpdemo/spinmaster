import sys
from unittest.mock import MagicMock

# Mock necessary modules before importing agent
for mod in ['google', 'google.adk', 'google.adk.agents', 'google.adk.tools', 
            'google.adk.models', 'google.adk.agents.callback_context', 
            'google.genai', 'google.genai.types', 'google.cloud', 
            'google.cloud.storage', 'google.cloud.secretmanager', 
            'google.adk.artifacts', 'google.adk.sessions', 
            'vertexai', 'vertexai.agent_engines', 'requests', 
            'google.auth', 'google.auth.transport', 'google.auth.transport.requests', 
            'google.oauth2']:
    sys.modules[mod] = MagicMock()

import os
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

import pytest
from unittest.mock import patch, AsyncMock
import agent

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def test_get_secret():
    """Test fetching secrets via Secret Manager."""
    with patch("agent.secretmanager.SecretManagerServiceClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "my_secret_value"
        mock_client.access_secret_version.return_value = mock_response

        secret_value = agent.get_secret("test_secret", "test_project")
        assert secret_value == "my_secret_value"
        mock_client.access_secret_version.assert_called_once()

def test_get_all_videos_with_urls():
    """Test listing videos from GCS permanent storage."""
    with patch("agent.storage.Client") as mock_storage_client_class:
        mock_client = mock_storage_client_class.return_value
        mock_blob1 = MagicMock()
        mock_blob1.name = "videos/"
        mock_blob2 = MagicMock()
        mock_blob2.name = "videos/video1.mp4"
        
        mock_client.list_blobs.return_value = [mock_blob1, mock_blob2]
        
        result = agent.get_all_videos_with_urls()
        
        assert len(result) == 1
        assert result[0]["filename"] == "video1.mp4"
        assert "storage.cloud.google.com" in result[0]["url"]

@pytest.mark.anyio
async def test_save_video_to_permanent_storage_tool_error():
    """Test video saving failure handling."""
    mock_tool_context = AsyncMock()
    mock_tool_context.load_artifact.side_effect = Exception("Artifact load failed")
    
    result = await agent.save_video_to_permanent_storage_tool(mock_tool_context, "test.mp4")
    assert "Error persisting video" in result

@pytest.mark.anyio
async def test_translate_video_tool_not_found():
    """Test translation request when video doesn't exist."""
    with patch("agent.storage.Client") as mock_storage_client_class:
        mock_client = mock_storage_client_class.return_value
        mock_bucket = mock_client.bucket.return_value
        mock_blob = mock_bucket.blob.return_value
        mock_blob.exists.return_value = False
        
        mock_tool_context = AsyncMock()
        result = await agent.translate_video_tool("missing.mp4", "Spanish", mock_tool_context)
        assert "Error: Source video not found" in result
