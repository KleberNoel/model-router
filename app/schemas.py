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
    system_prompt: str | None = None
    capabilities: dict = Field(default_factory=dict)
    is_active: bool = True


class UpdateModelRouteRequest(BaseModel):
    description: str | None = None
    upstream_base_url: str | None = None
    upstream_model_name: str | None = None
    upstream_headers: dict | None = None
    allowed_tenant_ids: list[str] | None = None
    max_context_tokens: int | None = None
    system_prompt: str | None = None
    capabilities: dict | None = None
    is_active: bool | None = None


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
    system_prompt: str | None
    capabilities_json: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OpenAIModelCard(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "model-router"
    context_window: int | None = None
    capabilities: dict = Field(default_factory=dict)


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: list[OpenAIModelCard]


class AgentProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    runtime: str = Field(pattern="^(goose|hermes|direct)$")
    model_route: str = Field(min_length=1, max_length=120)
    fallback_route: str | None = None
    workspace: str | None = None
    tools: list[str] = Field(default_factory=list)
    permission_mode: str = Field(default="ask", pattern="^(ask|auto|readonly)$")
    memory_scope: str = Field(default="project", pattern="^(user|tenant|project|run)$")


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    runtime: str
    model_route: str
    fallback_route: str | None
    workspace: str | None
    tools_json: list[str]
    permission_mode: str
    memory_scope: str
    is_active: bool


class AgentRunRequest(BaseModel):
    profile_id: str
    input_text: str = Field(min_length=1)


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    profile_id: str
    runtime: str
    model_route: str
    status: str
    input_text: str
    output_text: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AgentRunCompletionRequest(BaseModel):
    output_text: str | None = None
    error_message: str | None = None


class MemoryRequest(BaseModel):
    scope: str = Field(default="project", pattern="^(user|tenant|project|run)$")
    subject: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    source: str = "agent"
    importance: int = Field(default=50, ge=0, le=100)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    subject: str
    content: str
    source: str
    importance: int
    created_at: datetime
    updated_at: datetime


class TodoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: datetime | None = None
    notes: str | None = None


class TodoUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    completed: bool | None = None
    due_at: datetime | None = None
    notes: str | None = None


class TodoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    completed: bool
    due_at: datetime | None
    notes: str | None
    external_provider: str | None
    external_id: str | None
    created_at: datetime
    updated_at: datetime
