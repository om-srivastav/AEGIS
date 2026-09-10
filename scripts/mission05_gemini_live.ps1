# Run this script in a local PowerShell terminal from the canonical repository.
# The two hidden prompts configure each role separately; the same legitimate key
# can be entered at both prompts. No key is placed in command history or a file.
[CmdletBinding()]
param(
    [string]$AgentModel = 'gemini-3.7-flash',
    [string]$DiagnosticModel = 'gemini-3.7-flash'
)

$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'The canonical repository Python environment is missing.'
}

function Set-RoleSecret([string]$VariableName, [string]$Prompt) {
    $secureValue = Read-Host $Prompt -AsSecureString
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        [Environment]::SetEnvironmentVariable(
            $VariableName,
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer),
            'Process'
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
        $secureValue.Dispose()
    }
    if (-not [Environment]::GetEnvironmentVariable($VariableName, 'Process')) {
        throw 'A role key was empty; no live request was sent.'
    }
}

Push-Location -LiteralPath $repoPath
try {
    $env:PYTHONPATH = 'backend'
    $env:PYTEST_DEBUG_TEMPROOT = Join-Path $repoPath ('.pytest_cache\m05-' + [Guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $env:PYTEST_DEBUG_TEMPROOT -Force | Out-Null
    $env:AEGIS_AGENT_ADAPTER = 'deterministic'
    $env:AEGIS_REASONING_PROVIDER = 'deterministic'
    $env:AEGIS_RUN_LIVE_TESTS = '0'
    $env:AEGIS_RUN_COMBINED_LIVE_TEST = '0'
    & $pythonPath -m pytest backend/tests -m 'not live' -q
    if ($LASTEXITCODE -ne 0) { throw 'Deterministic verification failed. Live execution stopped.' }

    $env:AEGIS_AUT_PROVIDER = 'gemini'
    $env:AEGIS_AUT_MODEL = $AgentModel
    $env:AEGIS_DIAG_PROVIDER = 'gemini'
    $env:AEGIS_DIAG_MODEL = $DiagnosticModel
    Set-RoleSecret 'AEGIS_AUT_API_KEY' 'Agent Gemini API key (hidden)'
    Set-RoleSecret 'AEGIS_DIAG_API_KEY' 'Diagnostic Gemini API key (hidden)'

    # Explicit role-specific live budgets; no change to application defaults.
    $env:AEGIS_AUT_MAX_STEPS = '12'
    $env:AEGIS_AUT_MAX_TOOL_CALLS = '10'
    $env:AEGIS_AUT_MAX_PROVIDER_REQUESTS = '24'
    $env:AEGIS_AUT_MAX_TOTAL_TOKENS = '32768'
    $env:AEGIS_AUT_MAX_OUTPUT_TOKENS = '2048'
    $env:AEGIS_AUT_WALL_SECONDS = '90'
    $env:AEGIS_AUT_REQUEST_SECONDS = '30'
    $env:AEGIS_DIAG_MAX_STEPS = '1'
    $env:AEGIS_DIAG_MAX_PROVIDER_REQUESTS = '2'
    $env:AEGIS_DIAG_MAX_TOTAL_TOKENS = '16384'
    $env:AEGIS_DIAG_MAX_OUTPUT_TOKENS = '4096'
    $env:AEGIS_DIAG_WALL_SECONDS = '60'
    $env:AEGIS_DIAG_REQUEST_SECONDS = '45'
    $env:AEGIS_LIVE_OUTPUT_DIR = Join-Path $repoPath ('artifacts\m05-live\' + [Guid]::NewGuid().ToString())
    $env:AEGIS_RUN_LIVE_TESTS = '1'

    & $pythonPath -m pytest backend/tests/live/test_mission05_live.py::test_live_model_agent_sales_report -m live -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Agent live check failed. See the saved evidence. No automatic retry.' }
    & $pythonPath -m pytest backend/tests/live/test_mission05_live.py::test_live_model_diagnosis_remains_grounded -m live -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Diagnosis live check failed. See the saved evidence. No automatic retry.' }

    $env:AEGIS_RUN_COMBINED_LIVE_TEST = '1'
    & $pythonPath -m pytest backend/tests/live/test_mission05_live.py::test_live_combined_agent_and_diagnosis -m live -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Combined live check failed. See the saved evidence. No automatic retry.' }
    Write-Output ('All three live checks passed. Evidence: ' + $env:AEGIS_LIVE_OUTPUT_DIR)
}
finally {
    Remove-Item Env:AEGIS_AUT_API_KEY, Env:AEGIS_DIAG_API_KEY -ErrorAction SilentlyContinue
    $env:AEGIS_RUN_LIVE_TESTS = '0'
    $env:AEGIS_RUN_COMBINED_LIVE_TEST = '0'
    Remove-Item Env:PYTEST_DEBUG_TEMPROOT -ErrorAction SilentlyContinue
    Pop-Location
}
