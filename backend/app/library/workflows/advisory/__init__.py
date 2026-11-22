"""The Advisory Agent's workflows (not agent-visible):

    analysis.py   facts and numbers of an acquisition from the company data: DCF, price, customers, contracts,
                  technology, people, and scenarios that change the drivers — no recommendation
    opinion.py    the Advisory model's judgement as a record, and its checks against the evidence of the run
    store.py      verified judgements with a copy of their evidence, per system (history, prior judgement, reports)
    report.py     the 투자심의 보고서 PDF, drawn from a stored judgement only
"""
