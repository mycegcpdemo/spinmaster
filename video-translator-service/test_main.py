import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import os

# Set dummy environment variables to avoid real GCP client initialization errors
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
os.environ["BUCKET_NAME"] = "test-bucket"

# Mock the entire google cloud and genai libraries before importing main
import sys
sys.modules['google.cloud.storage'] = MagicMock()
sys.modules['google.cloud.speech_v2'] = MagicMock()
sys.modules['google.cloud.speech_v2.types'] = MagicMock()
sys.modules['google.api_core'] = MagicMock()
sys.modules['google.api_core.client_options'] = MagicMock()
sys.modules['google.cloud.translate_v3'] = MagicMock()
sys.modules['google.cloud.secretmanager'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()

import main

client = TestClient(main.app)

@patch("main.process_translation_workflow")
@patch("main.upload_blob")
@patch("main.download_blob")
def test_translate_raw(mock_download, mock_upload, mock_process, tmp_path):
    # Mocking the translation workflow
    mock_process.return_value = "gs://test-bucket/output.mp4"
    
    # Mock download_blob to create a dummy output file
    def side_effect_download(uri, path):
        with open(path, "wb") as f:
            f.write(b"mock output video data")
    mock_download.side_effect = side_effect_download
    
    # Create a dummy input video
    dummy_input = tmp_path / "dummy.mp4"
    dummy_input.write_bytes(b"dummy input")
    
    with open(dummy_input, "rb") as f:
        response = client.post(
            "/translate-raw",
            files={"file": ("dummy.mp4", f, "video/mp4")},
            data={"target_language": "Spanish"}
        )
    
    assert response.status_code == 200
    assert response.content == b"mock output video data"
    mock_process.assert_called_once()
    mock_upload.assert_called_once()

@patch("main.genai_client.models.generate_content")
def test_analyze_video_vibes(mock_generate_content):
    mock_response = MagicMock()
    mock_response.text = '[{"style_instruction": "energetic"}]'
    mock_generate_content.return_value = mock_response
    
    segments = [{"start_offset": 0.0, "end_offset": 1.0, "text": "Buy now!"}]
    enriched = main.analyze_video_vibes("gs://fake/video.mp4", segments)
    
    assert len(enriched) == 1
    assert enriched[0]["style_instruction"] == "energetic"

def test_get_secret():
    with patch("main.secretmanager.SecretManagerServiceClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "secret_value"
        mock_client.access_secret_version.return_value = mock_response
        
        result = main.get_secret("my-secret", "my-project")
        assert result == "secret_value"
        mock_client.access_secret_version.assert_called_once()
