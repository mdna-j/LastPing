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
    runtime_metrics_module = sys.modules.get("src.runtime_metrics")
    if runtime_metrics_module is not None:
        reset_request_metrics = getattr(runtime_metrics_module, "reset_request_metrics", None)
        if callable(reset_request_metrics):
            reset_request_metrics()
    os.environ.pop("DATABASE_URL", None)
