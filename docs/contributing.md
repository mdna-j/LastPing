# Contributing

Thanks for considering a contribution! A few guidelines to make reviews easier:

- Run tests and linters before PRs: `python -m pytest -q`, `ruff check .`, `black --check .`.
- Keep changes small and focused; add tests for new behavior.
- Follow existing code style and naming conventions.
- For database changes, add alembic migrations under `alembic/versions`.

Open an issue first if the change is large or needs design discussion.
