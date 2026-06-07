from __future__ import annotations


class PermissionCode:
    USERS_MANAGE = "users.manage"
    GROUPS_MANAGE = "groups.manage"
    WORKERS_READ = "workers.read"
    WORKERS_MANAGE = "workers.manage"
    TASKS_CREATE = "tasks.create"
    TASKS_READ_OWN = "tasks.read_own"
    TASKS_MANAGE_OWN = "tasks.manage_own"
    TASKS_READ_ALL = "tasks.read_all"
    TASKS_MANAGE_ALL = "tasks.manage_all"
    WORKER_PROTOCOL_USE = "worker_protocol.use"
    ASSETS_USE = "assets.use"
    ENROLL_MANAGE = "enroll.manage"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.USERS_MANAGE,
            cls.GROUPS_MANAGE,
            cls.WORKERS_READ,
            cls.WORKERS_MANAGE,
            cls.TASKS_CREATE,
            cls.TASKS_READ_OWN,
            cls.TASKS_MANAGE_OWN,
            cls.TASKS_READ_ALL,
            cls.TASKS_MANAGE_ALL,
            cls.WORKER_PROTOCOL_USE,
            cls.ASSETS_USE,
            cls.ENROLL_MANAGE,
        ]


PERMISSION_DESCRIPTIONS: dict[str, str] = {
    PermissionCode.USERS_MANAGE: "管理数据库用户",
    PermissionCode.GROUPS_MANAGE: "管理分组和分组权限",
    PermissionCode.WORKERS_READ: "查看基础机器状态",
    PermissionCode.WORKERS_MANAGE: "添加、启停、删除机器",
    PermissionCode.TASKS_CREATE: "创建评测任务",
    PermissionCode.TASKS_READ_OWN: "查看自己的任务",
    PermissionCode.TASKS_MANAGE_OWN: "操作自己的任务",
    PermissionCode.TASKS_READ_ALL: "查看全部任务",
    PermissionCode.TASKS_MANAGE_ALL: "操作全部任务",
    PermissionCode.WORKER_PROTOCOL_USE: "使用 worker 协议",
    PermissionCode.ASSETS_USE: "拉取资产和上传结果归档",
    PermissionCode.ENROLL_MANAGE: "生成和访问 enroll 脚本",
}

BUILTIN_GROUPS: dict[str, dict[str, str]] = {
    "admin": {"display_name": "管理员组", "description": "拥有系统全部权限"},
    "user": {"display_name": "普通用户组", "description": "创建和管理自己的评测任务"},
    "bot": {"display_name": "机器用户组", "description": "worker 机器通信使用"},
}

DEFAULT_GROUP_PERMISSIONS: dict[str, list[str]] = {
    "admin": PermissionCode.all(),
    "user": [
        PermissionCode.TASKS_CREATE,
        PermissionCode.TASKS_READ_OWN,
        PermissionCode.TASKS_MANAGE_OWN,
        PermissionCode.WORKERS_READ,
    ],
    "bot": [
        PermissionCode.WORKER_PROTOCOL_USE,
        PermissionCode.ASSETS_USE,
    ],
}
