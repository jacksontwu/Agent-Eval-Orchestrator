from app.schema.common import ApiModel


class DatasetInfo(ApiModel):
    dataset_ref: str
    available: bool
    path: str


class DatasetsResponse(ApiModel):
    datasets: list[DatasetInfo]
