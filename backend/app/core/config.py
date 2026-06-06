from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.defaults import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SHARED_ROOT, DEFAULT_HARBOR_REPO

# Resolve the project-root .env absolutely so every entry point (uvicorn, alembic,
# worker daemon) loads the same file regardless of the current working directory.
# A backend-local .env (if present) takes precedence over the repo-root one.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILES = (str(_REPO_ROOT / ".env"), str(_REPO_ROOT / "backend" / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEO_", env_file=_ENV_FILES, extra="ignore")

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    shared_root: Path = Field(default=DEFAULT_SHARED_ROOT)
    harbor_repo: Path = Field(default=DEFAULT_HARBOR_REPO)
    token: str | None = Field(default=None, alias="AEO_TOKEN")
    allow_no_auth: bool = Field(default=False, alias="AEO_ALLOW_NO_AUTH")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    @model_validator(mode="after")
    def _finalize(self) -> "Settings":
        # Resolve a relative shared_root against the repo root so the data location
        # is stable regardless of the process's current working directory.
        if not self.shared_root.is_absolute():
            object.__setattr__(self, "shared_root", _REPO_ROOT / self.shared_root)
        if not self.database_url:
            db = self.shared_root / "controller" / "aeo.db"
            object.__setattr__(self, "database_url", f"sqlite:///{db}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
