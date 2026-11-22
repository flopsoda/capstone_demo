"""query_enterprise_kg bound to this system's Enterprise KG (its meeting records and its news)."""

from ....library.tools.query_enterprise_kg import make_query_enterprise_kg
from .. import KG_STORE

query_enterprise_kg = make_query_enterprise_kg(KG_STORE)
