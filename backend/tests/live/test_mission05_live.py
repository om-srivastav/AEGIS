import asyncio
import os

import pytest

from app.models.schemas import PerturbationKind, RunStatus
from app.providers.model_config import build_transport, read_model_config
from app.providers.model_reasoning import ModelReasoningProvider
from app.services.diagnosis import DiagnosisService
from app.services.runner import RunService

pytestmark = pytest.mark.live


def require_live_role(prefix: str) -> None:
    if os.environ.get("AEGIS_RUN_LIVE_TESTS") != "1":
        pytest.skip("live model tests are disabled")
    required = (f"{prefix}_PROVIDER", f"{prefix}_MODEL", f"{prefix}_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip("live provider configuration is incomplete")


def test_live_model_agent_sales_report():
    require_live_role("AEGIS_AUT")
    config = read_model_config("AEGIS_AUT")
    transport = build_transport(config)
    service = RunService()
    try:
        run = asyncio.run(service.execute_model(
            PerturbationKind.NONE,
            transport=transport,
            model=config.model,
            limits=config.limits,
            secrets=(config.api_key,),
        ))
    finally:
        asyncio.run(transport.aclose())
    assert run.model_execution["agent"]["provider"] == config.provider
    assert run.status == RunStatus.PASSED


def test_live_model_diagnosis_remains_grounded():
    require_live_role("AEGIS_DIAG")
    config = read_model_config("AEGIS_DIAG")
    transport = build_transport(config)
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    provider = ModelReasoningProvider(
        transport,
        config.model,
        config.limits,
        audit_factory=lambda request: service.make_audit(request.run.run_id, secrets=(config.api_key,)),
    )
    try:
        diagnosis = asyncio.run(DiagnosisService(service).diagnose(run.run_id, provider=provider))
    finally:
        asyncio.run(transport.aclose())
    assert diagnosis.provider == provider.name
    assert run.model_execution["diagnostic"]["provider"] == config.provider
