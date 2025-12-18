"""
Test DAG for Docker Model Runner Integration

This DAG verifies that Airflow can connect to Docker Model Runner
and execute LLM inference using the OpenAI-compatible API.

Prerequisites:
1. Enable Docker Model Runner in Docker Desktop Settings
2. Pull models: docker model pull ai/llama3.2:3B-Q8_0
"""
import os
import json
import logging
from datetime import datetime

from airflow.decorators import dag, task

logger = logging.getLogger(__name__)

# Docker Model Runner configuration from environment
OPENAI_API_BASE = os.environ.get('OPENAI_API_BASE', 'http://model-runner.docker.internal/engines/llama.cpp/v1')
LLM_MODEL = os.environ.get('LLM_MODEL', 'ai/llama3.2:3B-Q8_0')


@dag(
    dag_id='test-docker-model-runner',
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'llm', 'agentic'],
    doc_md=__doc__,
)
def test_model_runner_dag():
    """Test DAG to verify Docker Model Runner connectivity."""

    @task
    def check_model_runner_health():
        """Verify Docker Model Runner is accessible."""
        import urllib.request
        import urllib.error

        health_url = f"{OPENAI_API_BASE}/models"
        logger.info(f"Checking Model Runner health at: {health_url}")

        try:
            req = urllib.request.Request(health_url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                logger.info(f"Model Runner response: {json.dumps(data, indent=2)}")
                return {"status": "healthy", "models": data}
        except urllib.error.URLError as e:
            logger.error(f"Model Runner health check failed: {e}")
            raise

    @task
    def test_chat_completion(health_result: dict):
        """Test a simple chat completion request."""
        import urllib.request

        chat_url = f"{OPENAI_API_BASE}/chat/completions"
        logger.info(f"Testing chat completion at: {chat_url}")

        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": "You are a helpful medical imaging assistant."},
                {"role": "user", "content": "What is DICOM? Answer in one sentence."}
            ],
            "max_tokens": 100,
            "temperature": 0.7
        }

        req = urllib.request.Request(
            chat_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )

        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            content = result['choices'][0]['message']['content']
            logger.info(f"LLM Response: {content}")
            return {
                "model": LLM_MODEL,
                "response": content,
                "usage": result.get('usage', {})
            }

    @task
    def report_results(chat_result: dict):
        """Log final test results."""
        logger.info("=" * 60)
        logger.info("Docker Model Runner Test Results")
        logger.info("=" * 60)
        logger.info(f"Model: {chat_result['model']}")
        logger.info(f"Response: {chat_result['response']}")
        logger.info(f"Token Usage: {chat_result.get('usage', 'N/A')}")
        logger.info("=" * 60)
        return chat_result

    # DAG flow
    health = check_model_runner_health()
    chat = test_chat_completion(health)
    report_results(chat)


# Instantiate the DAG
test_model_runner_dag()
