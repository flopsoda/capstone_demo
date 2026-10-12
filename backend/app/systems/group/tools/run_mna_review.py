"""run_mna_review — the Group system's instance, bound to this system's data/companies.json."""

from __future__ import annotations

from ....library.tools.run_mna_review import make_run_mna_review
from .. import DATA_DIR

run_mna_review = make_run_mna_review(DATA_DIR / "companies.json")
