from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_home_serves_ui():
    r = client.get("/")
    assert r.status_code == 200
    assert "AEGIS" in r.text


def test_run_api_baseline():
    r = client.post("/api/runs", json={"perturbation": "none"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "passed"
    assert data["score"] == 1.0


def test_diagnosis_api_round_trip():
    run_resp = client.post("/api/runs", json={"perturbation": "file_moved"})
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]
    diag_resp = client.post(f"/api/v1/runs/{run_id}/diagnoses")
    assert diag_resp.status_code == 200
    diagnosis = diag_resp.json()
    assert diagnosis["status"] == "supported"
    get_resp = client.get(f"/api/v1/diagnoses/{diagnosis['diagnosis_id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["diagnosis_id"] == diagnosis["diagnosis_id"]


def test_repair_and_evaluation_api_round_trip():
    run_resp = client.post("/api/runs", json={"perturbation": "file_moved"})
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]
    diag_resp = client.post(f"/api/v1/runs/{run_id}/diagnoses")
    diagnosis_id = diag_resp.json()["diagnosis_id"]
    repair_resp = client.post(f"/api/v1/diagnoses/{diagnosis_id}/repairs")
    assert repair_resp.status_code == 200
    repair_id = repair_resp.json()["repair_id"]
    eval_resp = client.post(
        f"/api/v1/repairs/{repair_id}/evaluations",
        headers={"Idempotency-Key": "api-round-trip"},
    )
    assert eval_resp.status_code == 200
    data = eval_resp.json()
    assert data["decision"] == "ACCEPT"
    get_resp = client.get(f"/api/v1/evaluations/{data['evaluation_id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["evaluation_id"] == data["evaluation_id"]
