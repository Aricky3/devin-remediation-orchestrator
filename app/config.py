"""Application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Devin API (v3 organization sessions) ---
    devin_api_key: str = ""
    devin_org_id: str = ""
    devin_base_url: str = "https://api.devin.ai/v3"
    # Upper bound on ACU spend per remediation session (cost guardrail).
    devin_max_acu_limit: int = 25

    # --- GitHub ---
    github_token: str = ""
    # Repository the orchestrator watches and remediates, "owner/name".
    github_repo: str = "Aricky3/superset"
    github_api_url: str = "https://api.github.com"
    # Secret used to verify GitHub webhook HMAC signatures (X-Hub-Signature-256).
    github_webhook_secret: str = ""

    # --- Workflow labels ---
    remediate_label: str = "devin-remediate"
    in_progress_label: str = "devin-in-progress"
    done_label: str = "devin-remediated"

    # --- Scheduling / concurrency ---
    scan_interval_seconds: int = 60
    poll_interval_seconds: int = 30
    max_concurrent_sessions: int = 5
    # When true, the periodic scanner is allowed to auto-dispatch new sessions.
    # Set false to drive the system purely via webhooks / manual dispatch.
    scanner_enabled: bool = True

    # --- Storage ---
    database_path: str = "data/orchestrator.db"

    # --- Modes ---
    # When true, never call the real Devin API; simulate sessions locally.
    # Useful for demos / CI without spending ACUs.
    dry_run: bool = False
    log_level: str = "INFO"

    @property
    def repo_owner(self) -> str:
        return self.github_repo.split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.github_repo.split("/", 1)[1]

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.github_repo}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
