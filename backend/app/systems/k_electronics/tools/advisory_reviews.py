"""record_advisory_opinion · add_advisory_details · lookup_advisory_reviews bound to K Electronics's Advisory judgement store."""

from ....library.tools.advisory_reviews import make_advisory_review_tools
from .. import ADVISORY_STORE

record_advisory_opinion, add_advisory_details, lookup_advisory_reviews = make_advisory_review_tools(ADVISORY_STORE)
