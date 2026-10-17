"""Build the whole platform once, in a temporary folder, for all tests."""
import os
import tempfile

import pytest

os.environ["OPSLENS_DATA_DIR"] = tempfile.mkdtemp(prefix="opslens-test-")

import duckdb  # noqa: E402

from opslens.analytics.churn_model import train_and_score  # noqa: E402
from opslens.automation.engine import run_rules  # noqa: E402
from opslens.ingest.connectors import ingest  # noqa: E402
from opslens.ingest.entity_resolution import resolve  # noqa: E402
from opslens.sources.generate import generate  # noqa: E402
from opslens.warehouse.build import build  # noqa: E402
from opslens.warehouse.quality import run_checks  # noqa: E402


@pytest.fixture(scope="session")
def con():
    generate()
    c = duckdb.connect(":memory:")
    ingest(c)
    resolve(c)
    build(c)
    run_checks(c)
    train_and_score(c)
    run_rules(c)
    yield c
    c.close()


class FakeLLM:
    """Returns scripted plans and answers, and remembers every prompt it was sent."""
    name = "fake"

    def __init__(self, plans, external=True):
        self.plans, self.external, self.prompts = list(plans), external, []

    def json(self, system, prompt):
        self.prompts.append(prompt)
        if "planning step" in system:
            return self.plans.pop(0)
        return {"answer": "Answer from evidence.", "confidence": "high", "caveats": []}
