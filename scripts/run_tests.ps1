# Run the test suite using the project's virtualenv when present
$venv = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (Test-Path $venv) {
  & $venv -m pytest -q
} else {
  py -3 -m pytest -q
}
