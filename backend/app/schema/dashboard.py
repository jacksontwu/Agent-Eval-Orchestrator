from app.schema.common import ApiModel


class DashboardTask(ApiModel):
    run_id: str
    display_name: str
    owner: str
    status: str
    template_id: str
    latest_batch_id: str | None = None
    counts: dict[str, int] = {}
    updated_at: str


class DashboardTasksResponse(ApiModel):
    tasks: list[DashboardTask]
