from fastapi import APIRouter

from app.schema.datasets import DatasetsResponse
from app.service import dataset_service

router = APIRouter()


@router.get("/datasets", response_model=DatasetsResponse)
def list_datasets() -> DatasetsResponse:
    return DatasetsResponse(datasets=dataset_service.list_datasets())
