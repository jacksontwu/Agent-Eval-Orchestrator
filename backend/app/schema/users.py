from app.schema.common import ApiModel


class UserCreate(ApiModel):
    username: str
    display_name: str
    password: str
    groups: list[str]


class UserUpdate(ApiModel):
    display_name: str | None = None
    is_active: bool | None = None
    groups: list[str] | None = None


class PasswordReset(ApiModel):
    password: str


class UserRead(ApiModel):
    user_id: str
    username: str
    display_name: str
    is_active: bool
    groups: list[str]
    created_at: str
    updated_at: str
    last_login_at: str | None = None
