from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from app.models.schemas import Diagnosis, EvaluationRecord, RepairProposal, RunRecord, RunRequest
from app.services.diagnosis import DiagnosisService
from app.services.repair import RepairService, RepairValidationError
from app.services.runner import run_service

app = FastAPI(
    title="AEGIS",
    version="0.3.0",
    description="Agent reliability, diagnosis and recovery laboratory",
)

diagnosis_service = DiagnosisService(run_service)
repair_service = RepairService(run_service, diagnosis_service)
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "project": "AEGIS", "version": "0.3.0"}


@app.post("/api/runs", response_model=RunRecord)
def create_run(request: RunRequest):
    return run_service.execute(request.perturbation)


@app.get("/api/runs", response_model=list[RunRecord])
def list_runs():
    return run_service.list()


@app.get("/api/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str):
    record = run_service.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="run not found")
    return record


@app.post("/api/v1/runs/{run_id}/diagnoses", response_model=Diagnosis)
async def create_diagnosis(run_id: str):
    try:
        return await diagnosis_service.diagnose(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found")


@app.get("/api/v1/diagnoses/{diagnosis_id}", response_model=Diagnosis)
def get_diagnosis(diagnosis_id: str):
    diagnosis = diagnosis_service.get(diagnosis_id)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    return diagnosis


@app.post("/api/v1/diagnoses/{diagnosis_id}/repairs", response_model=RepairProposal)
def create_repair(diagnosis_id: str):
    try:
        return repair_service.propose(diagnosis_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="diagnosis not found")
    except RepairValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/v1/repairs/{repair_id}", response_model=RepairProposal)
def get_repair(repair_id: str):
    repair = repair_service.get_repair(repair_id)
    if not repair:
        raise HTTPException(status_code=404, detail="repair not found")
    return repair


@app.post("/api/v1/repairs/{repair_id}/evaluations", response_model=EvaluationRecord)
def create_evaluation(repair_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try:
        return repair_service.evaluate(repair_id, idempotency_key=idempotency_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="repair not found")
    except RepairValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/v1/evaluations/{evaluation_id}", response_model=EvaluationRecord)
def get_evaluation(evaluation_id: str):
    evaluation = repair_service.get_evaluation(evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return evaluation
