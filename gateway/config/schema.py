from pydantic import BaseModel, Field
from typing import Optional


class FilterConfig(BaseModel):
    tags: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)


class ExcludeConfig(BaseModel):
    tags: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)


class ServiceConfig(BaseModel):
    name: str
    host: Optional[str] = None
    port: int = 8000
    docs_path: str = "/openapi.json"
    prefix: Optional[str] = None
    auth_required: Optional[bool] = None
    auth_override_paths: list[str] = Field(default_factory=list)
    filter: Optional[FilterConfig] = None
    exclude: Optional[ExcludeConfig] = None


class GatewayConfig(BaseModel):
    services: list[ServiceConfig] = Field(default_factory=list)
