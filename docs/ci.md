# CI Notes

This project recommends a CI pipeline that:

- installs dependencies
- runs linting (`ruff`)
- checks formatting (`black --check`)
- runs the test suite (`pytest`)

Example GitHub Actions config can live at `.github/workflows/ci.yml`.

If you want, I can add a `.github/workflows/ci.yml` workflow file configured for your preferred
matrix of Python versions and linters.
