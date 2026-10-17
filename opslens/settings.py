"""Paths and settings shared by every layer."""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs_corpus"
DATA_DIR = Path(os.getenv("OPSLENS_DATA_DIR", ROOT / "data"))
SOURCES_DIR = DATA_DIR / "sources"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"
POWERBI_EXPORT_DIR = DATA_DIR / "powerbi"

# The day the platform "runs" on. Generated data stops here, so every
# "today" in the platform means this date, not the real clock.
AS_OF_DATE = date(2026, 9, 30)
HISTORY_START = date(2024, 1, 1)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # gemini | ollama
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
