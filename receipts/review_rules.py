"""
Heuristic rules to determine needs_review
"""
from .config import REVIEW_RULES

_REASON_TAGS = {
    "total missing": "missing total",
    "date missing": "missing date",
    "description too short": "short description",
    "negative total": "negative total",
    "total exceeds threshold": "large total",
    "tax exceeds total": "tax exceeds total",
    "unknown doc_type": "doc type unknown",
    "utility bill (verify charges)": "utility bill",
}


def evaluate(transaction : dict) -> tuple[bool, str]:
    reasons = []
    if transaction.get("total") is None:
        reasons.append("total missing")
    if transaction.get("date") is None:
        reasons.append("date missing")

    desc = transaction.get("description") or ""
    if len(desc.strip()) < 2:
        reasons.append("description too short")

    total = transaction.get("total")
    if total is not None:
        if total < 0:
            reasons.append("negative total")
        if total > REVIEW_RULES["max_total"]:
            reasons.append("total exceeds threshold")

    gst = transaction.get("gst") or 0
    pst = transaction.get("pst") or 0
    if total is not None and (gst > total or pst > total):
        reasons.append("tax exceeds total")

    doc_type = transaction.get("doc_type") or ""
    if doc_type == "unknown" or doc_type == "":
        reasons.append("unknown doc_type")
    if doc_type == "utility_bill":
        reasons.append("utility bill (verify charges)")

    note = (transaction.get("note") or "").lower()
    for keyword in REVIEW_RULES["note_keywords"]:
        if keyword.lower() in note:
            reasons.append(f"note: {keyword}")
            break

    needs_review = len(reasons) > 0

    if needs_review:
        tags = []
        for r in reasons:
            if r in _REASON_TAGS:
                tags.append(f"[REVIEW: {_REASON_TAGS[r]}]")
            elif r.startswith("note: "):
                tags.append(f"[REVIEW: note {r[6:]}]")
            else:
                tags.append(f"[REVIEW: {r}]")
        tag_text = " ".join(tags)
        existing = transaction.get("note")
        transaction["note"] = f"{existing} {tag_text}" if existing else tag_text

    return needs_review, "; ".join(reasons)
