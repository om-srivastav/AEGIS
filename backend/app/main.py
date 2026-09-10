from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models.schemas import Diagnosis, EvaluationRecord, Mission04EvaluationRequest, RegressionCase, RegressionCaseCreate, RepairProposal, RunRecord, RunRequest
from app.services.diagnosis import DiagnosisService
from app.services.repair import RepairService, RepairValidationError
from app.services.regression import RegressionVaultService, RegressionValidationError
from app.services.runner import run_service
from app.providers.model_config import build_transport, read_settings
from app.providers.model_reasoning import ModelReasoningProvider

app = FastAPI(
    title="AEGIS",
    version="0.5.0",
    description="Agent reliability, diagnosis and recovery laboratory",
)

diagnosis_service = DiagnosisService(run_service)
repair_service = RepairService(run_service, diagnosis_service)
regression_service = RegressionVaultService(run_service, repair_service)
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"

# Vite production builds reference fingerprinted files below /assets.
# Mount only when the directory exists so backend-only development/tests keep working.
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "project": "AEGIS", "version": "0.5.0"}


@app.post("/api/runs", response_model=RunRecord)
async def create_run(request: RunRequest):
    try:
        settings = read_settings()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if settings.agent_adapter == "deterministic":
        return run_service.execute(request.perturbation)

    config = settings.agent
    assert config is not None
    transport = build_transport(config)
    try:
        return await run_service.execute_model(
            request.perturbation,
            transport=transport,
            model=config.model,
            limits=config.limits,
            secrets=(config.api_key,),
        )
    finally:
        close = getattr(transport, "aclose", None)
        if close is not None:
            await close()


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
        settings = read_settings()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        if settings.reasoning_provider == "deterministic":
            return await diagnosis_service.diagnose(run_id)

        config = settings.diagnostic
        assert config is not None
        transport = build_transport(config)
        provider = ModelReasoningProvider(
            transport,
            config.model,
            config.limits,
            audit_factory=lambda request: run_service.make_audit(
                request.run.run_id, secrets=(config.api_key,)
            ),
        )
        try:
            return await diagnosis_service.diagnose(run_id, provider=provider)
        finally:
            close = getattr(transport, "aclose", None)
            if close is not None:
                await close()
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


@app.post("/api/v1/regression-cases", response_model=RegressionCase)
def create_regression_case(request: RegressionCaseCreate):
    try:
        return regression_service.register_case(
            request.source_run_id, name=request.name, description=request.description
        )
    except RegressionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/v1/regression-cases", response_model=list[RegressionCase])
def list_regression_cases():
    return regression_service.list_cases()


@app.get("/api/v1/regression-cases/{case_id}/versions/{version}", response_model=RegressionCase)
def get_regression_case(case_id: str, version: int):
    case = regression_service.get_case(case_id, version)
    if not case:
        raise HTTPException(status_code=404, detail="regression case not found")
    return case


@app.post("/api/v1/repairs/{repair_id}/suite-evaluations", response_model=EvaluationRecord)
def create_suite_evaluation(
    repair_id: str,
    request: Mission04EvaluationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        return regression_service.evaluate_suite(
            repair_id,
            current_case_id=request.current_case_id,
            clean_case_id=request.clean_case_id,
            historical_case_ids=request.historical_case_ids,
            idempotency_key=idempotency_key,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="repair not found")
    except (RepairValidationError, RegressionValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
