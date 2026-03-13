import os
import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_database_engine():
    yield
    db_module = sys.modules.get("src.db")
    if db_module is not None:
        dispose_engine = getattr(db_module, "dispose_engine", None)
        if callable(dispose_engine):
            dispose_engine()
    os.environ.pop("DATABASE_URL", None)
