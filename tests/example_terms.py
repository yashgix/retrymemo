"""Made-up private terms for the redaction tests, in the format of replay/private_terms.json."""

import hashlib

from replay import redact

TERMS = {
    "public": {"northwind": "sendward", "globex": "citecheck"},
    "in_paths": {"orbit": "crossslot"},
    "replace": {"jdoe42": "<owner>", "Jane": "the owner"},
    "owner_emails": [hashlib.sha256(b"owner@example.com").hexdigest()],
    "strip": [r"\bBatch [WS]\d\d,?\s*"],
    "drop": [r"pitch notes"],
    "drop_sections": ["pitch story"],
    "private": [r"jane|pitch notes|\bBatch [WS]\d\d\b"],
    "blocked": [
        r"jdoe42|\bjane\b|acme-work|pitch notes|pitch story|globex"
        r"|\b(northwind|orbit)-(base|m\d)\b|\b(northwind|orbit)/(repo|records)\b"
    ],
    "targeted": [
        [r"^results/chaos-[\w-]+/report\.json$", "northwind-m1", "sendward-m1"],
        [r"^acceptance/tests/m1/hygiene\.test\.ts$", "/jdoe42|", "/"],
    ],
    "reviewer": "acme",
}
RULES = redact.rules(TERMS)
