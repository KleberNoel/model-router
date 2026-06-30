from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TenantSummary(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    role: str | None = None


class UserSummary(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    is_platform_admin: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    tenant_slug: str | None = None


class SwitchTenantRequest(BaseModel):
    tenant_slug: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserSummary
    tenant: TenantSummary


class AuthMeResponse(BaseModel):
    auth_type: str
    user: UserSummary | None = None
    tenant: TenantSummary
    scopes: list[str]


class LoginTenantSelectionResponse(BaseModel):
    detail: str
    available_tenants: list[TenantSummary]


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    metadata: dict = Field(default_factory=dict)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    is_platform_admin: bool = False


class GrantMembershipRequest(BaseModel):
    user_id: str
    tenant_id: str
    role: str = "member"


class CreateApiKeyRequest(BaseModel):
    tenant_id: str
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["chat:completions", "models:read"])


class CreateApiKeyResponse(BaseModel):
    api_key: str
    key_prefix: str
    tenant_id: str
    name: str
    scopes: list[str]


class CreateModelRouteRequest(BaseModel):
    name: str
    upstream_base_url: str
    upstream_model_name: str
    description: str | None = None
    upstream_headers: dict = Field(default_factory=dict)
    allowed_tenant_ids: list[str] = Field(default_factory=list)
    max_context_tokens: int | None = None
    is_active: bool = True


class ModelRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    upstream_base_url: str
    upstream_model_name: str
    upstream_headers_json: dict
    allowed_tenant_ids: list[str]
    max_context_tokens: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OpenAIModelCard(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "model-router"
    context_window: int | None = None


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: list[OpenAIModelCard]
