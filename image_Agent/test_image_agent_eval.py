import os
import sys
import pytest

try:
    import google.adk
    HAS_ADK = True
except ImportError:
    HAS_ADK = False

if not HAS_ADK:
    from unittest.mock import MagicMock
    # Mock out missing modules for headless local collection if ADK is missing
    for mod in ['google', 'google.adk', 'google.adk.agents', 'google.adk.tools', 
                'google.adk.models', 'google.adk.agents.callback_context', 
                'google.genai', 'google.genai.types', 'google.cloud', 
                'google.cloud.storage', 'google.cloud.secretmanager', 
                'google.adk.artifacts', 'google.adk.sessions', 
                'google.adk.evaluation', 'google.adk.evaluation.agent_evaluator', 
                'vertexai', 'vertexai.agent_engines']:
        sys.modules[mod] = MagicMock()
    os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "us-central1"

import agent

@pytest.mark.skipif(not HAS_ADK, reason="google.adk is not installed, skipping real evaluation")
def test_agent_evaluation():
    from google.adk.evaluation.agent_evaluator import AgentEvaluator
    
    # Establish real FDE-compliant holistic evaluation suite
    result = AgentEvaluator.evaluate(
        agent=agent.root_agent,
        dataset_path="image_agent.evalset.json",
        config_path="test_config.json"
    )
    
    assert result.is_successful, "Agent evaluation failed FDE criteria."
