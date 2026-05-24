from concurrent.futures import ThreadPoolExecutor


def test_update_task_template_executor_config_merges_concurrent_worker_paths(store):
    template = store.create_task_template(
        owner="default",
        name="cfg-concurrent",
        dataset_ref="/tmp/dataset",
        executor_kind="harbor-docker",
        executor_config={"useAssetSync": True, "datasetPathByWorker": {}},
        model_profile_ref=None,
        note="",
    )
    worker_ids = ["worker-a", "worker-b", "worker-c"]

    def patch_worker(worker_id: str) -> None:
        store.update_task_template_executor_config(
            template["template_id"],
            {"datasetPathByWorker": {worker_id: f"/sync/{worker_id}/dataset"}},
        )

    with ThreadPoolExecutor(max_workers=len(worker_ids)) as pool:
        list(pool.map(patch_worker, worker_ids))

    updated = store.get_task_template(template["template_id"])
    paths = updated["executor_config"]["datasetPathByWorker"]
    assert set(paths.keys()) == set(worker_ids)
    for worker_id in worker_ids:
        assert paths[worker_id] == f"/sync/{worker_id}/dataset"
