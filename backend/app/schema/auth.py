from app.schema.common import ApiModel


class LoginRequest(ApiModel):
    username: str
    password: str


class PrincipalRead(ApiModel):
    username: str
    source: str
    groups: list[str]
    permissions: list[str]


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: PrincipalRead
