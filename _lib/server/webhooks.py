"""
server/webhooks.py -- Flask webhook handlers for NocoDB events.

Rules:
- Do not log webhook secrets, headers, or full payloads.
- Debug endpoint logs structural summaries only.
- Real handlers return 200 for ignored payloads and per-row write failures to
  avoid NocoDB retry storms.
"""

import hmac
import json
import logging
import os
import sys

from flask import Blueprint, jsonify, request

import data_access
from server import bookkeeping_annual_automation, people_bridging, period_automation
from config.settings import NOCODB_WEBHOOK_SECRET

# Allow importing ReminderAutomation from Scripts root (two levels above _lib)
_SCRIPTS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

logger = logging.getLogger(__name__)

engagement_bp = Blueprint("engagement", __name__)

COMPANY_FILED_UNTIL_FIELDS = {
    "GST Filed Until",
    "GST Frequency",
    "PST Filed Until",
    "PST Frequency",
    "T2 Filed Until",
}

COMPANY_FILED_UNTIL_GROUP_FIELDS = {
    "GST": {"GST Filed Until", "GST Frequency"},
    "PST": {"PST Filed Until", "PST Frequency"},
    "T2": {"T2 Filed Until"},
}

BUSINESS_STATUS_FIELDS = (
    *period_automation.BUSINESS_STATUS_FIELDS.keys(),
    bookkeeping_annual_automation.BOOKKEEPING_STATUS_FIELD,
    bookkeeping_annual_automation.ANNUAL_RETURN_STATUS_FIELD,
)

COMPANY_OWNER_FIELDS = {"Owner Name", "Owner Email"}


def _status_value(field):
    """Handle select fields represented as dicts or plain strings."""
    if isinstance(field, dict):
        return field.get("value") or field.get("title")
    return field


def _safe_sample(value, limit=50):
    """Return a short scalar sample without logging full sensitive fields."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    return str(value)[:limit]


def _field_type_and_sample(record, candidates):
    """Find the first candidate field in a dict and summarize type/sample."""
    if not isinstance(record, dict):
        return None
    lower_map = {str(key).lower(): key for key in record.keys()}
    for candidate in candidates:
        actual_key = lower_map.get(candidate.lower())
        if actual_key is None:
            continue
        raw_value = record.get(actual_key)
        value = _status_value(raw_value)
        return {
            "field": actual_key,
            "type": type(raw_value).__name__,
            "value_type": type(value).__name__,
            "value_sample": _safe_sample(value),
        }
    return None


def _summarize_record(record):
    """Summarize row-like dict structure without logging full values."""
    if not isinstance(record, dict):
        return None
    return {
        "field_names": sorted(record.keys()),
        "row_id": _field_type_and_sample(record, ("Id", "id", "row_id")),
        "status": _field_type_and_sample(record, ("Status", "status")),
    }


def _extract_nocodb_row_pairs(payload: dict) -> list[tuple[dict, dict]]:
    """Extract after/before row pairs from supported NocoDB webhook payloads."""
    data = payload.get("data")
    if isinstance(data, dict):
        rows = data.get("rows")
        previous_rows = data.get("previous_rows")
        if isinstance(rows, list) and isinstance(previous_rows, list):
            return [
                (after, before)
                for after, before in zip(rows, previous_rows)
                if isinstance(after, dict) and isinstance(before, dict)
            ]
    return []


def _extract_nocodb_inserted_rows(payload: dict) -> list[dict]:
    """Extract newly-inserted rows from a NocoDB records.after.insert payload."""
    data = payload.get("data")
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _has_company_filed_until_change(after: dict, before: dict) -> bool:
    """Return true when a Filed Until source field or frequency changed."""
    return any(after.get(field) != before.get(field) for field in COMPANY_FILED_UNTIL_FIELDS)


def _has_company_owner_change(after: dict, before: dict) -> bool:
    """Return true when Owner Name or Owner Email changed."""
    return any(after.get(field) != before.get(field) for field in COMPANY_OWNER_FIELDS)


def _has_person_company_change(after: dict, before: dict) -> bool:
    """Return true when the legacy People.Company text field changed."""
    return after.get("Company") != before.get("Company")


def _changed_company_filed_until_groups(after: dict, before: dict) -> set[str]:
    """Return GST/PST/T2 groups whose source fields changed."""
    changed = set()
    for group, fields in COMPANY_FILED_UNTIL_GROUP_FIELDS.items():
        if any(after.get(field) != before.get(field) for field in fields):
            changed.add(group)
    return changed


def _filter_reasons_for_changed_groups(reasons: list[str], changed_groups: set[str]) -> list[str]:
    """Keep calculation reasons relevant to the changed source groups."""
    return [
        reason for reason in reasons
        if any(reason.startswith(group) for group in changed_groups)
    ]


def _has_business_status_change(after: dict, before: dict) -> bool:
    """Return true when one of the GST/PST/T2 business status fields changed."""
    return any(after.get(field) != before.get(field) for field in BUSINESS_STATUS_FIELDS)


def _create_business_status_review_event(row: dict, result) -> None:
    """Append a Needs Review event for a safe business-status skip."""
    engagement_ref = row.get("Company") or row.get("Id") or row.get("id") or "Unknown Engagement"
    data_access.create_event(
        engagement_id=engagement_ref,
        event_type="Needs Review",
        description=(
            "Business status Filed Until automation review: "
            f"{result.review_reason or result.reason}"
        ),
        actor="NocoDB Webhook",
    )


def _validate_nocodb_secret():
    """Validate X-Webhook-Secret against NOCODB_WEBHOOK_SECRET."""
    if not NOCODB_WEBHOOK_SECRET:
        logger.warning("Webhook rejected: NOCODB_WEBHOOK_SECRET not configured")
        return jsonify({"status": "rejected", "reason": "secret_not_configured"}), 401

    header_secret = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(header_secret, NOCODB_WEBHOOK_SECRET):
        logger.warning("Webhook rejected: invalid secret")
        return jsonify({"status": "rejected", "reason": "invalid_secret"}), 401

    return None


@engagement_bp.route("/webhook/engagement-updated", methods=["POST"])
def engagement_updated():
    """
    Handle NocoDB records.after.update webhooks from the Engagements table.

    If Status changed, append a Status Changed row to Events through
    data_access.py. Ignored payloads and per-row write failures return 200 to
    avoid retry storms.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            logger.warning("Received non-JSON or non-dict payload")
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type")
        if event_type != "records.after.update":
            logger.info("Ignoring event_type=%s", event_type)
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        data = payload.get("data")
        if not isinstance(data, dict):
            logger.info("Ignoring webhook without data dict")
            return jsonify({"status": "ignored", "reason": "invalid_data"}), 200

        rows = data.get("rows")
        previous_rows = data.get("previous_rows")
        if not isinstance(rows, list) or not isinstance(previous_rows, list):
            logger.info("Ignoring webhook without rows/previous_rows lists")
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        if len(rows) != len(previous_rows):
            logger.warning(
                "rows/previous_rows length mismatch: %d vs %d",
                len(rows), len(previous_rows)
            )
            return jsonify({"status": "ignored", "reason": "rows_mismatch"}), 200

        recorded = 0
        for after, before in zip(rows, previous_rows):
            try:
                if not isinstance(after, dict) or not isinstance(before, dict):
                    continue
                if "Status" not in after or "Status" not in before:
                    continue

                after_status = _status_value(after.get("Status"))
                before_status = _status_value(before.get("Status"))
                if before_status == after_status:
                    continue

                row_id = after.get("Id") or after.get("id") or after.get("row_id")
                if row_id is None:
                    logger.warning("Skipping Status change without row id")
                    continue

                engagement_ref = after.get("Company") or row_id
                data_access.create_event(
                    engagement_id=engagement_ref,
                    event_type="Status Changed",
                    description=f"{before_status} -> {after_status}",
                    actor="NocoDB Webhook",
                )
                recorded += 1
                logger.info(
                    "Recorded: row=%s, %s -> %s",
                    row_id, before_status, after_status
                )

            except Exception as exc:
                row_id = after.get("Id") or after.get("id") if isinstance(after, dict) else None
                logger.error("Failed to record event for row %s: %s", row_id, exc)
                continue

        return jsonify({"status": "ok", "recorded": recorded}), 200

    except Exception:
        logger.exception("Unexpected error in engagement-updated webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/company-created", methods=["POST"])
def company_created():
    """
    Handle NocoDB records.after.insert webhooks from the Companies table.

    Detects/links the primary owner in People (Email > Name match), creating
    a new Person row when there's truly no match. Mode B: internal DB write
    only (no money/email/CRA), always Event-logged, ambiguous matches always
    go to needs_review instead of guessing. Secondary owners are out of scope
    (no Email field, higher misuse risk -- stay manual).
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            logger.warning("Received non-JSON or non-dict payload")
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type")
        if event_type != "records.after.insert":
            logger.info("Ignoring event_type=%s", event_type)
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        rows = _extract_nocodb_inserted_rows(payload)
        if not rows:
            logger.info("Ignoring company-created webhook without rows")
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        results = []
        for row in rows:
            try:
                result = people_bridging.resolve_or_create_primary_owner_for_company(row)
                results.append(result["action"])
                logger.info("Company %s created -> %s", row.get("Id"), result["action"])
            except Exception:
                logger.exception(
                    "Failed to process company-created webhook for row %s", row.get("Id")
                )
                results.append("error")

        return jsonify({"status": "ok", "results": results}), 200

    except Exception:
        logger.exception("Unexpected error in company-created webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/person-created", methods=["POST"])
def person_created():
    """
    Handle NocoDB records.after.insert webhooks from the People table.

    Detects/links the matching Company via the legacy Company text field
    (exact normalized Legal Name match), creating a minimal stub Company row
    when there's truly no match. Does not parse Related Companies (free
    text, can list multiple companies -- too ambiguous to auto-create from).
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            logger.warning("Received non-JSON or non-dict payload")
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type")
        if event_type != "records.after.insert":
            logger.info("Ignoring event_type=%s", event_type)
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        rows = _extract_nocodb_inserted_rows(payload)
        if not rows:
            logger.info("Ignoring person-created webhook without rows")
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        results = []
        for row in rows:
            try:
                result = people_bridging.resolve_or_create_company_for_person(row)
                results.append(result["action"])
                logger.info("Person %s created -> %s", row.get("Id"), result["action"])
            except Exception:
                logger.exception(
                    "Failed to process person-created webhook for row %s", row.get("Id")
                )
                results.append("error")

        return jsonify({"status": "ok", "results": results}), 200

    except Exception:
        logger.exception("Unexpected error in person-created webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/company-owner-updated", methods=["POST"])
def company_owner_updated():
    """
    Handle NocoDB records.after.update webhooks from the Companies table.

    NocoDB's grid UI inserts a row first (often blank) and fills fields via
    separate update calls -- so Owner Name/Email is frequently only known at
    update time, not at the original after-insert event that
    company-created listens for. Same resolver, triggered only when Owner
    Name or Owner Email actually changed (idempotent either way).
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            logger.warning("Received non-JSON or non-dict payload")
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        if payload.get("type") != "records.after.update":
            logger.info("Ignoring event_type=%s", payload.get("type"))
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        pairs = _extract_nocodb_row_pairs(payload)
        if not pairs:
            logger.info("Ignoring company-owner-updated webhook without rows/previous_rows")
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        results = []
        for after, before in pairs:
            try:
                if not _has_company_owner_change(after, before):
                    continue
                result = people_bridging.resolve_or_create_primary_owner_for_company(
                    after, actor="Webhook: Company Owner Updated"
                )
                results.append(result["action"])
                logger.info("Company %s owner updated -> %s", after.get("Id"), result["action"])
            except Exception:
                logger.exception(
                    "Failed to process company-owner-updated webhook for row %s", after.get("Id")
                )
                results.append("error")

        return jsonify({"status": "ok", "results": results}), 200

    except Exception:
        logger.exception("Unexpected error in company-owner-updated webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/person-company-updated", methods=["POST"])
def person_company_updated():
    """
    Handle NocoDB records.after.update webhooks from the People table.

    Same reasoning as company-owner-updated: the legacy Company text field
    is often filled in via a separate update after the row already exists.
    Triggered only when that field actually changed.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            logger.warning("Received non-JSON or non-dict payload")
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        if payload.get("type") != "records.after.update":
            logger.info("Ignoring event_type=%s", payload.get("type"))
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        pairs = _extract_nocodb_row_pairs(payload)
        if not pairs:
            logger.info("Ignoring person-company-updated webhook without rows/previous_rows")
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        results = []
        for after, before in pairs:
            try:
                if not _has_person_company_change(after, before):
                    continue
                result = people_bridging.resolve_or_create_company_for_person(
                    after, actor="Webhook: Person Company Updated"
                )
                results.append(result["action"])
                logger.info("Person %s company updated -> %s", after.get("Id"), result["action"])
            except Exception:
                logger.exception(
                    "Failed to process person-company-updated webhook for row %s", after.get("Id")
                )
                results.append("error")

        return jsonify({"status": "ok", "results": results}), 200

    except Exception:
        logger.exception("Unexpected error in person-company-updated webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/engagement-period-updated", methods=["POST"])
def engagement_period_updated():
    """
    Handle Engagement Status updates for recurring GST/PST/T2/Bookkeeping periods.

    Only a new transition into Status=Complete advances the next period.
    Failures return 200 to avoid NocoDB retry storms.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type") or payload.get("event_type")
        if event_type not in ("records.after.update", "rows.updated"):
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        row_pairs = _extract_nocodb_row_pairs(payload)
        if not row_pairs:
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        results = []
        for after, before in row_pairs:
            try:
                results.append(
                    period_automation.advance_completed_engagement(after, before)
                )
            except Exception as exc:
                row_id = after.get("Id") or after.get("id")
                logger.error(
                    "Failed period advancement for engagement row %s: %s",
                    row_id,
                    exc,
                    exc_info=True,
                )
                results.append({
                    "status": "error",
                    "row_id": row_id,
                    "reason": str(exc),
                })

        created = sum(1 for item in results if item.get("status") == "created")
        skipped = sum(1 for item in results if item.get("status") == "skipped")
        ignored = sum(1 for item in results if item.get("status") == "ignored")
        errors = sum(1 for item in results if item.get("status") == "error")

        return jsonify({
            "status": "ok",
            "created": created,
            "skipped": skipped,
            "ignored": ignored,
            "errors": errors,
        }), 200

    except Exception:
        logger.exception("Unexpected error in engagement-period-updated webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/company-filed-until-updated", methods=["POST"])
def company_filed_until_updated():
    """
    Recalculate Companies dependent date fields after Filed Until edits.

    All ignored payloads and per-row failures return 200 to avoid NocoDB retry
    storms. Live writes are routed through data_access.py.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type") or payload.get("event_type")
        if event_type not in ("records.after.update", "rows.updated"):
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        row_pairs = _extract_nocodb_row_pairs(payload)
        if not row_pairs:
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        updated = 0
        skipped = 0
        errors = 0
        reviews = 0
        created = 0

        for after, before in row_pairs:
            try:
                if not _has_company_filed_until_change(after, before):
                    skipped += 1
                    continue

                changed_groups = _changed_company_filed_until_groups(after, before)
                company_name = after.get("Legal Name")
                if not company_name:
                    reviews += 1
                    logger.warning("Company Filed Until update skipped: missing Legal Name")
                    continue

                calculation = period_automation.build_company_filed_until_update_fields(after)
                reasons = _filter_reasons_for_changed_groups(
                    calculation.review_reasons + calculation.skipped,
                    changed_groups,
                )

                if calculation.fields:
                    updated_row = data_access.update_company_by_legal_name(
                        company_name,
                        calculation.fields,
                    )
                    if updated_row is None:
                        reviews += 1
                        data_access.create_event(
                            engagement_id=company_name,
                            event_type="Needs Review",
                            description=(
                                "Filed Until calculation skipped: "
                                "Company Legal Name did not match exactly one row"
                            ),
                            actor="NocoDB Webhook",
                        )
                    else:
                        updated += 1
                        if (
                            "GST" in changed_groups
                            and after.get("Bookkeeping Filed Until")
                        ):
                            catchup = bookkeeping_annual_automation.ensure_next_bookkeeping_engagement(
                                {**after, **calculation.fields},
                                trigger_ref=after.get("Id") or company_name,
                            )
                            if catchup["status"] == "created":
                                created += 1
                            elif catchup["status"] == "needs_review":
                                reviews += 1
                else:
                    skipped += 1

                if reasons:
                    reviews += 1
                    data_access.create_event(
                        engagement_id=company_name,
                        event_type="Needs Review",
                        description="Filed Until calculation review: " + "; ".join(reasons),
                        actor="NocoDB Webhook",
                    )

            except Exception as exc:
                errors += 1
                row_id = after.get("Id") or after.get("id")
                logger.error(
                    "Failed company Filed Until calculation for row %s: %s",
                    row_id,
                    exc,
                    exc_info=True,
                )
                continue

        return jsonify({
            "status": "ok",
            "updated": updated,
            "skipped": skipped,
            "reviews": reviews,
            "created": created,
            "errors": errors,
        }), 200

    except Exception:
        logger.exception("Unexpected error in company-filed-until-updated webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/engagement-business-status-completed", methods=["POST"])
def engagement_business_status_completed():
    """
    Advance business cursors and create the next recurring work item.

    This route intentionally ignores legacy Status=Complete. Per-row failures
    return 200 to avoid NocoDB retry storms.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    try:
        payload = request.get_json(silent=True)
        if payload is None or not isinstance(payload, dict):
            return jsonify({"status": "ignored", "reason": "invalid_json"}), 200

        event_type = payload.get("type") or payload.get("event_type")
        if event_type not in ("records.after.update", "rows.updated"):
            return jsonify({"status": "ignored", "reason": "wrong_event_type"}), 200

        row_pairs = _extract_nocodb_row_pairs(payload)
        if not row_pairs:
            return jsonify({"status": "ignored", "reason": "invalid_rows"}), 200

        updated = 0
        skipped = 0
        ignored = 0
        reviews = 0
        created = 0
        errors = 0

        for after, before in row_pairs:
            try:
                if not _has_business_status_change(after, before):
                    ignored += 1
                    continue

                for status_field in BUSINESS_STATUS_FIELDS:
                    if status_field == bookkeeping_annual_automation.BOOKKEEPING_STATUS_FIELD:
                        result = bookkeeping_annual_automation.complete_bookkeeping_engagement(
                            after, before
                        )
                        if result["status"] == "ignored":
                            ignored += 1
                        elif result["status"] == "needs_review":
                            reviews += 1
                        elif result["status"] == "updated":
                            updated += 1
                            next_status = result.get("next", {}).get("status")
                            if next_status == "created":
                                created += 1
                            elif next_status == "needs_review":
                                reviews += 1
                        else:
                            skipped += 1
                        continue

                    if status_field == bookkeeping_annual_automation.ANNUAL_RETURN_STATUS_FIELD:
                        result = bookkeeping_annual_automation.complete_annual_return_engagement(
                            after, before
                        )
                        if result["status"] == "ignored":
                            ignored += 1
                        elif result["status"] == "needs_review":
                            reviews += 1
                        elif result["status"] == "created":
                            created += 1
                        else:
                            skipped += 1
                        continue

                    initial = period_automation.build_business_status_filed_until_update(
                        after,
                        before,
                        status_field,
                    )

                    if initial.status == "ignored":
                        ignored += 1
                        continue

                    if initial.status == "needs_review":
                        reviews += 1
                        _create_business_status_review_event(after, initial)
                        continue

                    company_rows = data_access.list_companies_by_legal_name(initial.company)
                    if len(company_rows) != 1:
                        reviews += 1
                        initial = period_automation.BusinessStatusFiledUntilUpdate(
                            status="needs_review",
                            reason="company_match_count_not_one",
                            status_field=status_field,
                            company=initial.company,
                            company_field=initial.company_field,
                            period_end=initial.period_end,
                            review_reason=(
                                f"{status_field} completed but Company Legal Name "
                                f"matched {len(company_rows)} rows"
                            ),
                        )
                        _create_business_status_review_event(after, initial)
                        continue

                    company_row = company_rows[0]
                    update_result = period_automation.build_business_status_filed_until_update(
                        after,
                        before,
                        status_field,
                        existing_filed_until=company_row.get(initial.company_field),
                    )

                    if update_result.status == "skipped":
                        skipped += 1
                        continue

                    if update_result.status == "needs_review":
                        reviews += 1
                        _create_business_status_review_event(after, update_result)
                        continue

                    if update_result.fields:
                        data_access.update_company_by_legal_name(
                            update_result.company,
                            update_result.fields,
                        )
                        updated += 1
                    else:
                        skipped += 1

            except Exception as exc:
                errors += 1
                row_id = after.get("Id") or after.get("id")
                logger.error(
                    "Failed business status Filed Until update for row %s: %s",
                    row_id,
                    exc,
                    exc_info=True,
                )
                continue

        return jsonify({
            "status": "ok",
            "updated": updated,
            "skipped": skipped,
            "ignored": ignored,
            "reviews": reviews,
            "created": created,
            "errors": errors,
        }), 200

    except Exception:
        logger.exception("Unexpected error in engagement-business-status-completed webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/annual-return-status", methods=["POST"])
def annual_return_status():
    """
    Update Annual Return tracker status from OpenClaw bot reports.
    Called by the WhatsApp bot when a staff member reports a filing as complete.

    Body: {"company": "...", "status": "Filed", "filed_by": "Riley"}
    Valid statuses: Filed | Confirmation Sent | Reminder Sent | Pending | Inactive
    """
    try:
        data = request.get_json(silent=True) or {}
        company  = str(data.get("company")  or "").strip()
        status   = str(data.get("status")   or "Filed").strip()
        filed_by = str(data.get("filed_by") or "").strip()

        if not company:
            return jsonify({"status": "error", "reason": "company is required"}), 400

        try:
            from ReminderAutomation.modules.annual_return import valid_statuses
            from ReminderAutomation.interfaces.tracker_writer import update_annual_return_status
        except ImportError as exc:
            logger.error("ReminderAutomation import failed: %s", exc)
            return jsonify({"status": "error", "reason": "module not available"}), 500

        if status not in valid_statuses():
            return jsonify({
                "status": "error",
                "reason": f"invalid status â€” use one of: {sorted(valid_statuses())}",
            }), 400

        result = update_annual_return_status(company, status, filed_by)
        if result.get("ok"):
            logger.info("Annual Return status updated: %s â†’ %s (by %s)", company, status, filed_by or "?")
            return jsonify({"status": "ok", "company": company, "new_status": status}), 200
        else:
            logger.warning("Annual Return update failed: %s â€” %s", company, result.get("error"))
            return jsonify({"status": "error", "reason": result.get("error")}), 422

    except Exception:
        logger.exception("Unexpected error in annual-return-status webhook")
        return jsonify({"status": "error", "message": "internal_error"}), 200


@engagement_bp.route("/webhook/debug", methods=["POST"])
def debug_webhook():
    """
    Debug endpoint for NocoDB webhook payload structure inspection.

    Requires X-Webhook-Secret header matching NOCODB_WEBHOOK_SECRET.
    Logs structural summary only; response stays intentionally small.
    """
    rejected = _validate_nocodb_secret()
    if rejected:
        return rejected

    payload = request.get_json(silent=True)
    if payload is None or not isinstance(payload, dict):
        logger.warning("Debug webhook received invalid JSON")
        return jsonify({"status": "rejected", "reason": "invalid_json"}), 400

    try:
        summary = {
            "top_level_keys": sorted(payload.keys()),
            "event_type": payload.get("event_type") or payload.get("type"),
        }

        items = payload.get("items")
        old_items = payload.get("old_items")
        summary["items_exists"] = items is not None
        summary["items_is_list"] = isinstance(items, list)
        summary["items_length"] = len(items) if isinstance(items, list) else None
        summary["old_items_exists"] = old_items is not None
        summary["old_items_is_list"] = isinstance(old_items, list)
        summary["old_items_length"] = len(old_items) if isinstance(old_items, list) else None

        data = payload.get("data")
        summary["data_exists"] = data is not None
        summary["data_type"] = type(data).__name__ if data is not None else None

        candidate_names = (
            "old", "new", "before", "after", "previous", "previous_rows",
            "current", "row", "record", "rows",
        )

        if isinstance(data, dict):
            summary["data_top_level_keys"] = sorted(data.keys())
            present_candidates = [key for key in candidate_names if key in data]
            summary["data_candidate_fields_present"] = present_candidates

            data_records = {}
            for key in present_candidates:
                value = data.get(key)
                if isinstance(value, dict):
                    data_records[key] = _summarize_record(value)
                elif isinstance(value, list):
                    item_summary = None
                    if value and isinstance(value[0], dict):
                        item_summary = _summarize_record(value[0])
                    data_records[key] = {
                        "type": "list",
                        "length": len(value),
                        "first_item": item_summary,
                    }
                else:
                    data_records[key] = {"type": type(value).__name__}
            summary["data_candidate_structures"] = data_records

            for current_key in ("new", "after", "current", "row", "record", "rows"):
                current_summary = data_records.get(current_key)
                if isinstance(current_summary, dict):
                    status = current_summary.get("status")
                    if not status and isinstance(current_summary.get("first_item"), dict):
                        status = current_summary["first_item"].get("status")
                    if status:
                        summary["status_candidate"] = status
                        break

            for old_key in ("old", "before", "previous", "previous_rows"):
                old_summary = data_records.get(old_key)
                if isinstance(old_summary, dict):
                    status = old_summary.get("status")
                    if not status and isinstance(old_summary.get("first_item"), dict):
                        status = old_summary["first_item"].get("status")
                    if status:
                        summary["old_status_candidate"] = status
                        break

        elif isinstance(data, list):
            summary["data_length"] = len(data)
            first_data_item = data[0] if data else None
            if isinstance(first_data_item, dict):
                summary["first_data_item_keys"] = sorted(first_data_item.keys())
                summary["first_data_item_summary"] = _summarize_record(first_data_item)

        logger.info("===== DEBUG WEBHOOK STRUCTURAL SUMMARY =====")
        logger.info("Summary: %s", json.dumps(summary, indent=2, ensure_ascii=False))

        return jsonify({"status": "ok"}), 200

    except Exception as exc:
        logger.error("Debug webhook structural parsing failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": "parsing_failed"}), 500

