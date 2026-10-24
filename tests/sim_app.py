"""App usado exclusivamente nos testes de subprocesso contra o simulador HTTP."""

import os

from google.adk.models.google_llm import Gemini

from aurora.api import create_app
from aurora.config import Settings

model = Gemini(model="gemini-3.1-flash-lite", base_url=os.environ["GEMINI_SIM_URL"])
app = create_app(Settings.load(), model)
