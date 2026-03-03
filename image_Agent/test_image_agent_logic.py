import sys
from unittest.mock import MagicMock

# Mock necessary modules before importing agent
for mod in ['google', 'google.adk', 'google.adk.agents', 'google.adk.tools', 
            'google.adk.models', 'google.adk.agents.callback_context', 
            'google.genai', 'google.genai.types', 'google.cloud', 
            'google.cloud.storage', 'google.cloud.secretmanager', 
            'google.adk.artifacts', 'google.adk.sessions', 
            'vertexai', 'vertexai.agent_engines']:
    sys.modules[mod] = MagicMock()

import os
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

import pytest
from unittest.mock import patch, AsyncMock
import agent
import asyncio

@pytest.fixture
def anyio_backend():
    return 'asyncio'

if not hasattr(asyncio, 'to_thread'):
    async def mock_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)
    asyncio.to_thread = mock_to_thread

def test_get_secret():
    """Test retrieving secrets via Secret Manager."""
    with patch("agent.secretmanager.SecretManagerServiceClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "my_secret_value"
        mock_client.access_secret_version.return_value = mock_response

        secret_value = agent.get_secret("test_secret", "test_project")
        assert secret_value == "my_secret_value"
        mock_client.access_secret_version.assert_called_once()

@pytest.mark.anyio
async def test_search_and_list_files_tool():
    """Test listing files from GCS permanent storage."""
    with patch("agent.storage.Client") as mock_storage_client_class:
        mock_client = mock_storage_client_class.return_value
        mock_blob1 = MagicMock()
        mock_blob1.name = "permanent_storage/image1.png"
        mock_blob2 = MagicMock()
        mock_blob2.name = "permanent_storage/image2.jpg"
        
        mock_client.list_blobs.return_value = [mock_blob1, mock_blob2]
        
        mock_tool_context = AsyncMock()
        result = await agent.search_and_list_files_tool(mock_tool_context)
        assert len(result) == 2
        assert "permanent_storage/image1.png" in result
        assert "permanent_storage/image2.jpg" in result

@pytest.mark.anyio
async def test_render_from_permanent_storage_tool_not_found():
    """Test behavior when image is not found in GCS."""
    with patch("agent.storage.Client") as mock_storage_client_class:
        mock_client = mock_storage_client_class.return_value
        mock_bucket = mock_client.bucket.return_value
        mock_blob = mock_bucket.blob.return_value
        
        mock_blob.exists.return_value = False
        
        mock_tool_context = AsyncMock()
        result = await agent.render_from_permanent_storage_tool(mock_tool_context, "missing.png")
        
        assert "not found" in result

@pytest.mark.anyio
async def test_save_session_to_gcs_tool_no_artifacts():
    """Test save session tool when no artifacts are present."""
    mock_tool_context = AsyncMock()
    mock_tool_context.list_artifacts.return_value = []
    
    result = await agent.save_session_to_gcs_tool(mock_tool_context)
    assert "No temporary files found" in result
