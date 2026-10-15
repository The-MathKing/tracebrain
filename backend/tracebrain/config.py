import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

FALKORDB_URL = os.getenv("FALKORDB_URL") or None
FALKORDB_LITE_PATH = os.getenv("FALKORDB_LITE_PATH", str(ROOT / "data" / "falkordb.db"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None
# OpenRouter (Anthropic-compatible endpoint): set OPENROUTER_API_KEY and we route the
# Anthropic SDK through https://openrouter.ai/api with a bearer token.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or None
LLM_BASE_URL = os.getenv("TRACEBRAIN_LLM_BASE_URL") or ("https://openrouter.ai/api" if OPENROUTER_API_KEY else None)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or None
MODEL = os.getenv("TRACEBRAIN_MODEL", "anthropic/claude-opus-5.5" if OPENROUTER_API_KEY else "claude-opus-5-5")
EXTRACT_MODEL = os.getenv("TRACEBRAIN_EXTRACT_MODEL", MODEL)
TENANT = os.getenv("TRACEBRAIN_TENANT", "falkordb")
ALLOW_GITHUB_WRITE = os.getenv("TRACEBRAIN_ALLOW_GITHUB_WRITE") == "1"


def llm_configured() -> bool:
    return bool(ANTHROPIC_API_KEY or OPENROUTER_API_KEY)


def make_anthropic_client():
    """The one place LLM clients are built. Works for Anthropic direct or OpenRouter."""
    import anthropic

    if not llm_configured():
        raise RuntimeError("No LLM key: set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env")
    if ANTHROPIC_API_KEY:
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, base_url=LLM_BASE_URL)
    return anthropic.Anthropic(auth_token=OPENROUTER_API_KEY, base_url=LLM_BASE_URL)
