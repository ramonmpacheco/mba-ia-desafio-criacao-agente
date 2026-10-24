import os

import pytest
from fastapi.testclient import TestClient
from google.adk.models.google_llm import Gemini

from aurora.api import create_app
from aurora.config import ROOT, Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path, ROOT / "dados", "gemini-3.1-flash-lite")


@pytest.fixture
def model(monkeypatch):
    # Credencial fictícia: nenhuma chave real é enviada ao simulador.
    monkeypatch.setenv("GOOGLE_API_KEY", "contract-test-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    return Gemini(
        model="gemini-3.1-flash-lite",
        base_url=os.getenv("GEMINI_SIM_URL", "http://127.0.0.1:4900/gemini"),
    )


@pytest.fixture
def client(settings, model):
    with TestClient(create_app(settings, model)) as client:
        yield client
