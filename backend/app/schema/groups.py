from app.schema.common import ApiModel


class GroupCreate(ApiModel):
    name: str
    display_name: str
    description: str = ""


class GroupUpdate(ApiModel):
    display_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class PermissionAssignment(ApiModel):
    permissions: list[str]


class PermissionRead(ApiModel):
    code: str
    description: str


class GroupRead(ApiModel):
    group_id: str
    name: str
    display_name: str
    description: str
    is_builtin: bool
    is_active: bool
    permissions: list[str]
