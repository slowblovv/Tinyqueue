import pytest

pytestmark = pytest.mark.integration


async def test_create_and_get_job(client):
    resp = await client.post(
        "/api/v1/jobs", json={"type": "echo", "payload": {"message": "hi"}}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["type"] == "echo"

    job_id = body["id"]
    resp2 = await client.get(f"/api/v1/jobs/{job_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == job_id


async def test_get_nonexistent_job_returns_404(client):
    resp = await client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"


async def test_create_job_unknown_type_rejected(client):
    resp = await client.post("/api/v1/jobs", json={"type": "not_a_real_handler"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_JOB_TYPE"


async def test_idempotency_key_returns_same_job(client):
    payload = {"type": "echo", "payload": {"message": "x"}, "idempotency_key": "dup-key-1"}
    r1 = await client.post("/api/v1/jobs", json=payload)
    r2 = await client.post("/api/v1/jobs", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


async def test_list_jobs_filters_by_status(client):
    await client.post("/api/v1/jobs", json={"type": "echo"})
    resp = await client.get("/api/v1/jobs", params={"status": "PENDING"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["status"] == "PENDING" for item in body["items"])


async def test_cancel_pending_job(client):
    create = await client.post("/api/v1/jobs", json={"type": "echo"})
    job_id = create.json()["id"]
    resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_delayed_job_created_with_future_available_at(client):
    resp = await client.post(
        "/api/v1/jobs", json={"type": "echo", "delay_seconds": 3600}
    )
    body = resp.json()
    assert body["created_at"] < body["available_at"]
