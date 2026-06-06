def test_harbor_viewer_global(client, monkeypatch):
    from app.service.orchestration import viewer_manager
    monkeypatch.setattr(
        viewer_manager, "ensure_global",
        lambda: {"viewerId": "global", "port": 18100, "url": "http://127.0.0.1:18100"},
    )
    resp = client.get("/api/harbor-viewer/global")
    assert resp.status_code == 200, resp.text
    assert resp.json()["port"] == 18100
