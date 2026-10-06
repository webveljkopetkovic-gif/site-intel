"""Central configuration. Every value can be overridden in .env or environment variables."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Claude ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    claude_max_tokens: int = 8000

    # --- AnythingLLM ---
    anythingllm_base_url: str = "http://localhost:3001"
    anythingllm_api_key: str = ""
    workspace_prefix: str = "site"  # workspace per site: site-vdigital-dev

    # --- Crawler ---
    max_pages_per_site: int = 25
    crawl_concurrency: int = 4
    request_delay_s: float = 0.5
    request_timeout_s: float = 20.0
    render_js: str = "auto"  # auto | always | never  (auto = Playwright only if raw HTML looks empty)
    min_text_chars: int = 200  # below this, "auto" mode re-renders the page with a real browser
    user_agent: str = "SiteIntelBot/1.0 (+https://vdigital.dev)"

    # --- Retrieval ---
    retrieval_top_n: int = 6
    retrieval_score_threshold: float = 0.2

    # --- Storage ---
    data_dir: Path = Path("data")

    # --- API ---
    api_token: str = ""  # if set, every API call needs header: Authorization: Bearer <token>


settings = Settings()
