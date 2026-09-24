import pytest

pytestmark = pytest.mark.integration


async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_endpoint_reports_database_ok(client):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


async def test_metrics_endpoint_exposes_prometheus_text(client):
    await client.post("/api/v1/jobs", json={"type": "echo"})
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "jobs_created_total" in resp.text
    assert "queue_depth" in resp.text
