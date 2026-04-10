from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Namespace
    namespace: Optional[str] = None           # None = modalità legacy gateway.*

    # Auth
    auth_jwks_url: Optional[str] = None
    auth_required: bool = True
    auth_mode: str = "validate"               # relay | validate
    auth_jwks_ttl_seconds: int = 300

    # Claims relay (mode=validate)
    auth_claim_id: str = "sub"
    auth_claim_email: str = "email"
    auth_claim_roles: str = "roles"

    # Admin
    admin_secret: Optional[str] = None

    # Logging
    log_level: str = "INFO"

    # Discovery
    docs_default: str = "/openapi.json"
    config_path: str = "/config/services.json"
    docker_socket: str = "unix://var/run/docker.sock"
    docker_networks: Optional[str] = None   # comma-separated network names; auto-detected if unset
    discovery_retry_attempts: int = 5
    discovery_retry_backoff: float = 2.0

    class Config:
        env_prefix = "GATEWAY_"


settings = Settings()
