"""
server/bot_routes.py -- Flask routes for WhatsApp bot NocoDB queries and updates.

Two endpoints:
  GET  /bot/engagement?company=<ref>&type=<payroll|gst|pst|t2>
  POST /bot/engagement/update

Company ref: 4 digits = suffix match for numeric companies (e.g. "3635"),
             otherwise substring match on Legal Name / Operating Name.

Status writes for T2/GST/PST replace the entire MultiSelect field, clearing
all existing stages and blockers. This is intentional — choosing a new stage
resets the field and triggers the webhook auto-calculation.
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path

from flask import Blueprint, jsonify, request

import config.settings as settings
import data_access
from server import bookkeeping_annual_automation, tax_engagement_prepare

# ReminderAutomation is a top-level package (sibling of _lib), not under
# config/data_access's existing sys.path entry -- add the Scripts root too.
_SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from ReminderAutomation.config import TAX_REMINDER_SHEETS  # noqa: E402
from ReminderAutomation.interfaces import nocodb as rem_nocodb  # noqa: E402
from ReminderAutomation.interfaces import tracker_reader  # noqa: E402
from ReminderAutomation.interfaces import wechat as rem_wechat  # noqa: E402
from ReminderAutomation.interfaces import whatsapp as rem_whatsapp  # noqa: E402
from ReminderAutomation.modules import tax_reminder  # noqa: E402
from receipts import rename_receipts as receipt_rename  # noqa: E402

logger = logging.getLogger(__name__)
bot_bp = Blueprint("bot", __name__)

_PAYROLL_STAGE_OPTIONS = [
    settings.NOCODB_ENGAGEMENT_STATUS_WAITING_INFO,
    settings.NOCODB_ENGAGEMENT_STATUS_PROCESSING,
    settings.NOCODB_ENGAGEMENT_STATUS_COMPLETED,
    settings.NOCODB_ENGAGEMENT_STATUS_ARCHIVED,
]

_TAX_STAGE_OPTIONS = [
    "stage | not started",
    "stage | bookkeeping pending",
    "stage | client info pending",
    "stage | preparing",
    "stage | client approval pending",
    "stage | ready to file",
    "stage | filed",
    "stage | completed",
    "stage | archived",
]

_TYPE_CONFIG = {
    "Payroll": {
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_STATUS,
        "stage_options": _PAYROLL_STAGE_OPTIONS,
        "done_statuses": {
            settings.NOCODB_ENGAGEMENT_STATUS_COMPLETED,
            settings.NOCODB_ENGAGEMENT_STATUS_ARCHIVED,
        },
    },
    "T2": {
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_T2_STATUS,
        "stage_options": _TAX_STAGE_OPTIONS,
        "done_statuses": {"stage | completed", "stage | archived"},
    },
    "GST": {
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_GST_STATUS,
        "stage_options": _TAX_STAGE_OPTIONS,
        "done_statuses": {"stage | completed", "stage | archived"},
    },
    "PST": {
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_PST_STATUS,
        "stage_options": _TAX_STAGE_OPTIONS,
        "done_statuses": {"stage | completed", "stage | archived"},
    },
}

_TYPE_ALIASES = {
    "payroll": "Payroll",
    "t2": "T2",
    "gst": "GST",
    "pst": "PST",
}


def _read_status(engagement: dict, status_field: str) -> str:
    """Read status from an engagement row, handling SingleSelect and MultiSelect formats."""
    raw = engagement.get(status_field, "")
    if isinstance(raw, list):
        return ", ".join(str(v) for v in raw if v)
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("title") or str(raw)
    return str(raw or "")


@bot_bp.route("/bot/engagements", methods=["GET"])
def list_engagements_query():
    """
    Flexible engagement query for natural-language bot requests.

    Query params (all optional):
      type        -- comma-separated types: PST,GST,T2,Payroll
      priority    -- comma-separated priority prefixes: P0,P1
      urgent      -- true → equivalent to priority=P0,P1
      blocked     -- true → engagements with a blocker | status
      review      -- true → engagements at stage | review (Alex's queue)
      assigned_to -- exact name: Riley | Morgan | Sherry | Alex
      due_period  -- this_month | this_week | overdue
      limit       -- max results (default 25)
    """
    raw_types = request.args.get("type", "")
    types = [t.strip() for t in raw_types.split(",") if t.strip()] or None

    raw_prio = request.args.get("priority", "")
    priorities = [p.strip() for p in raw_prio.split(",") if p.strip()] or None

    urgent = request.args.get("urgent", "").lower() == "true"
    if urgent:
        priorities = ["P0", "P1"]

    blocked = request.args.get("blocked", "").lower() == "true"
    review = request.args.get("review", "").lower() == "true"
    assigned_to = request.args.get("assigned_to", "").strip() or None
    due_period = request.args.get("due_period", "").strip() or None

    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25

    try:
        results = data_access.query_engagements(
            types=types,
            priorities=priorities,
            assigned_to=assigned_to,
            due_period=due_period,
            blocked=blocked,
            review=review,
            limit=limit,
        )
        return jsonify({"count": len(results), "engagements": results}), 200
    except Exception as exc:
        logger.error("Bot engagements query failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@bot_bp.route("/bot/engagement/refresh", methods=["POST"])
def refresh_engagements():
    """
    Trigger an on-demand GST/PST/T2 Engagement refresh (the same scan that
    otherwise only runs via the daily Task Scheduler job).

    Body (JSON):
      actor  -- who/which account triggered this, for traceability (required)
      apply  -- whether to actually write changes (default true; bot-triggered
                refreshes are expected to write, not dry-run)
      today  -- optional ISO date override, mainly for testing
    """
    body = request.get_json(silent=True) or {}
    actor = (body.get("actor") or "").strip()
    apply = body.get("apply", True)
    today_raw = (body.get("today") or "").strip()

    if not actor:
        return jsonify({"error": "actor is required"}), 400

    try:
        today = date.fromisoformat(today_raw) if today_raw else date.today()
    except ValueError:
        return jsonify({"error": f"invalid today value: {today_raw}"}), 400

    try:
        result = tax_engagement_prepare.scan_tax_engagements(
            today=today, apply=apply, actor=f"Bot:{actor}"
        )
    except Exception as exc:
        logger.error("Bot engagement refresh failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    if result.get("locked"):
        return jsonify(result), 409
    return jsonify(result), 200


@bot_bp.route("/bot/engagement", methods=["GET"])
def find_engagement():
    """
    Find active engagements by company reference and type.

    Query params:
      company  -- 4-digit suffix (numeric company) or name substring
      type     -- payroll | gst | pst | t2
    """
    company_ref = (request.args.get("company") or "").strip()
    type_raw = (request.args.get("type") or "").strip()

    if not company_ref or not type_raw:
        return jsonify({"error": "company and type are required"}), 400

    eng_type = _TYPE_ALIASES.get(type_raw.lower())
    if not eng_type:
        return jsonify({"error": f"unsupported type: {type_raw}. Use: payroll, gst, pst, t2"}), 400

    cfg = _TYPE_CONFIG[eng_type]

    companies = data_access.search_companies_by_ref(company_ref)
    if not companies:
        return jsonify({
            "found": 0,
            "engagements": [],
            "message": f"找不到公司: {company_ref}",
        }), 200

    engagements = []
    for company in companies:
        company_name = company.get("Legal Name", "")
        for e in data_access.find_active_engagements(company_name, eng_type, cfg["done_statuses"]):
            engagements.append({
                "id": e.get("Id"),
                "name": e.get("Name", ""),
                "company": company_name,
                "type": eng_type,
                "current_status": _read_status(e, cfg["status_field"]),
                "status_field": cfg["status_field"],
                "stage_options": cfg["stage_options"],
                "period_end": (
                    e.get(settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END)
                    or e.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END)
                ),
            })

    return jsonify({"found": len(engagements), "engagements": engagements}), 200


@bot_bp.route("/bot/engagement/update", methods=["POST"])
def update_engagement():
    """
    Update an engagement's status field and append a Status Changed event.

    Body (JSON):
      engagement_id  -- NocoDB row Id (integer)
      status_field   -- field name to update (e.g. "Status", "T2 Status")
      new_status     -- chosen stage option string
      actor          -- staff name shown in Events (e.g. "Riley")
      company        -- company name used as the Events log reference
    """
    body = request.get_json(silent=True) or {}
    engagement_id = body.get("engagement_id")
    status_field = (body.get("status_field") or "").strip()
    new_status = (body.get("new_status") or "").strip()
    actor = (body.get("actor") or "Bot").strip()
    company = (body.get("company") or "").strip()

    if not engagement_id or not status_field or not new_status:
        return jsonify({"error": "engagement_id, status_field, and new_status are required"}), 400

    valid_status_fields = {cfg["status_field"] for cfg in _TYPE_CONFIG.values()}
    if status_field not in valid_status_fields:
        return jsonify({
            "error": f"status_field must be one of {sorted(valid_status_fields)}; "
                     f"use POST /bot/record/update for any other field"
        }), 400

    try:
        result = data_access.update_engagement_status(
            engagement_id=engagement_id,
            status_field=status_field,
            new_status=new_status,
            actor=actor,
            company_ref=company,
        )
        return jsonify({"ok": True, "updated": result}), 200
    except Exception as exc:
        logger.error("Bot update failed for engagement %s: %s", engagement_id, exc)
        return jsonify({"error": str(exc)}), 500


_UPDATABLE_TABLES = {"Companies", "Engagements", "People"}


@bot_bp.route("/bot/record/update", methods=["POST"])
def update_record():
    """
    Update arbitrary content fields on a Companies/Engagements/People row.

    System-managed or computed fields (Id, CreatedAt, formulas, etc.) are
    rejected — see data_access.get_protected_field_names.

    Body (JSON):
      table      -- "Companies" | "Engagements" | "People"
      record_id  -- NocoDB row Id (integer)
      fields     -- dict of field_name -> new value
      actor      -- who/which account triggered this, for traceability
    """
    body = request.get_json(silent=True) or {}
    table = (body.get("table") or "").strip()
    record_id = body.get("record_id")
    fields = body.get("fields")
    actor = (body.get("actor") or "").strip()

    if table not in _UPDATABLE_TABLES:
        return jsonify({"error": f"table must be one of {sorted(_UPDATABLE_TABLES)}"}), 400
    if not record_id or not isinstance(fields, dict) or not fields:
        return jsonify({"error": "record_id and a non-empty fields dict are required"}), 400
    if not actor:
        return jsonify({"error": "actor is required"}), 400

    try:
        result = data_access.update_record(
            table=table, record_id=record_id, fields=fields, actor=actor
        )
        return jsonify({"ok": True, "updated": result}), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Bot record update failed for %s %s: %s", table, record_id, exc)
        return jsonify({"error": str(exc)}), 500


@bot_bp.route("/bot/people", methods=["GET"])
def find_people():
    """
    Find People rows by name substring, so the bot can resolve a record Id
    before calling /bot/record/update.

    Query params:
      name  -- substring to match against Full Name (required)
    """
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    people = data_access.search_people_by_name(name)
    return jsonify({
        "found": len(people),
        "people": [
            {
                "id": p.get("Id"),
                "full_name": p.get("Full Name", ""),
                "email": p.get("Email", ""),
                "phone": p.get("Phone", ""),
                "company": p.get("Company", ""),
            }
            for p in people
        ],
    }), 200


@bot_bp.route("/bot/companies", methods=["GET"])
def find_companies():
    """
    Find Companies rows by name/ref, so the bot can resolve a record Id and
    see current field values before calling /bot/record/update.

    Query params:
      name  -- 4-digit suffix or name substring (same matching as /bot/engagement's
                company param), required
    """
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    protected = data_access.get_protected_field_names("Companies")
    companies = data_access.search_companies_by_ref(name)
    return jsonify({
        "found": len(companies),
        "companies": [
            {
                "id": c.get("Id"),
                "fields": {k: v for k, v in c.items() if k not in protected and k != "Id"},
            }
            for c in companies
        ],
    }), 200


def _load_tax_reminder_rows(sheet: str) -> list[dict]:
    """Same data source as ReminderAutomation.runners.tax_reminder_preview:
    NocoDB first (currently a stub returning None), falling back to the Excel
    tracker."""
    rows = rem_nocodb.get_tax_reminder_clients(sheet)
    if rows is None:
        rows = tracker_reader.load_tax_tracker(sheet)
    return rows


@bot_bp.route("/bot/tax-reminders", methods=["GET"])
def tax_reminders_preview():
    """
    Preview which clients are due for a Tax Reminder round today, without
    sending anything. Read-only — does not post to the staff WhatsApp/WeChat
    group; use POST /bot/tax-reminders/deliver for that after confirmation.

    Query params:
      sheet  -- one of Bookkeeping, PST, GST, T2 (default: all)
    """
    sheet_raw = (request.args.get("sheet") or "").strip()
    sheets = [sheet_raw] if sheet_raw else TAX_REMINDER_SHEETS
    invalid = [s for s in sheets if s not in TAX_REMINDER_SHEETS]
    if invalid:
        return jsonify({
            "error": f"unknown sheet(s) {invalid}; use one of {TAX_REMINDER_SHEETS}"
        }), 400

    today = date.today()
    sheets_result = {}
    for sheet in sheets:
        try:
            rows = _load_tax_reminder_rows(sheet)
        except (FileNotFoundError, ValueError) as exc:
            sheets_result[sheet] = {"error": str(exc)}
            continue
        clients = tax_reminder.get_due_clients(rows, sheet, today)
        sheets_result[sheet] = {
            "count": len(clients),
            "preview": tax_reminder.format_preview(clients, sheet, today),
        }

    return jsonify({"run_date": today.isoformat(), "sheets": sheets_result}), 200


@bot_bp.route("/bot/tax-reminders/deliver", methods=["POST"])
def tax_reminders_deliver():
    """
    Actually post a Tax Reminder preview to the staff WhatsApp/WeChat group,
    on demand -- the same effect as the scheduled daily run, for one sheet.
    Caller must have already shown the preview (GET /bot/tax-reminders) and
    gotten explicit confirmation before calling this.

    Body (JSON):
      sheet  -- one of Bookkeeping, PST, GST, T2 (required)
      actor  -- who/which account triggered this, for traceability (required)
    """
    body = request.get_json(silent=True) or {}
    sheet = (body.get("sheet") or "").strip()
    actor = (body.get("actor") or "").strip()

    if sheet not in TAX_REMINDER_SHEETS:
        return jsonify({"error": f"sheet must be one of {TAX_REMINDER_SHEETS}"}), 400
    if not actor:
        return jsonify({"error": "actor is required"}), 400

    today = date.today()
    try:
        rows = _load_tax_reminder_rows(sheet)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 500

    clients = tax_reminder.get_due_clients(rows, sheet, today)
    if not clients:
        return jsonify({"sent": False, "count": 0, "message": "nothing due, nothing sent"}), 200

    preview = tax_reminder.format_preview(clients, sheet, today)
    wa_ok = rem_whatsapp.send_to_group(preview)
    rem_wechat.send_to_group(preview)
    logger.info(
        "Bot-triggered tax reminder delivery: sheet=%s actor=%s count=%d wa_ok=%s",
        sheet, actor, len(clients), wa_ok,
    )
    if not wa_ok:
        return jsonify({"error": "WhatsApp delivery failed", "count": len(clients)}), 500
    return jsonify({"sent": True, "count": len(clients)}), 200


@bot_bp.route("/bot/bookkeeping/refresh", methods=["POST"])
def refresh_bookkeeping():
    """
    On-demand catch-up: ensure the next missing monthly Bookkeeping
    Engagement exists for one company, up to its current GST Filed Until
    cursor. Mirrors bookkeeping_annual_automation.ensure_next_bookkeeping_engagement,
    which otherwise only fires automatically off a GST Filed Until webhook.

    Idempotent (checks recurring_engagement_exists before creating), so no
    confirmation step is required -- same risk profile as
    POST /bot/engagement/refresh.

    Body (JSON):
      company  -- company name or 4-digit ref (required)
      actor    -- who/which account triggered this, for traceability (required)
    """
    body = request.get_json(silent=True) or {}
    company_ref = (body.get("company") or "").strip()
    actor = (body.get("actor") or "").strip()

    if not company_ref:
        return jsonify({"error": "company is required"}), 400
    if not actor:
        return jsonify({"error": "actor is required"}), 400

    companies = data_access.search_companies_by_ref(company_ref)
    if not companies:
        return jsonify({"found": 0, "message": f"找不到公司: {company_ref}"}), 200
    if len(companies) > 1:
        return jsonify({
            "found": len(companies),
            "companies": [c.get("Legal Name", "") for c in companies],
            "message": "多个公司匹配，请提供更精确的名称",
        }), 200

    try:
        result = bookkeeping_annual_automation.ensure_next_bookkeeping_engagement(
            companies[0], trigger_ref=f"Bot:{actor}"
        )
    except Exception as exc:
        logger.error("Bot bookkeeping refresh failed for %s: %s", company_ref, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(result), 200


def _build_rename_plan(folder: Path) -> tuple[list[dict], list[str]]:
    files, skipped = receipt_rename.collect_files(folder)
    client = receipt_rename.AIClient(timeout=180, max_retries=1)
    used_names: dict[str, set] = {}
    plan = []
    for path in files:
        result = receipt_rename.analyze_file(client, path)
        new_name, status = receipt_rename.build_new_name(result, path, used_names)
        plan.append({
            "old_path": str(path),
            "new_path": str(path.with_name(new_name)),
            "status": status,
            "provider": result.get("provider"),
            "confidence": result.get("confidence"),
            "error": result.get("error"),
        })
    return plan, skipped


@bot_bp.route("/bot/receipts/rename-preview", methods=["GET"])
def receipts_rename_preview():
    """
    Preview an AI-based rename plan ("date vendor amount") for receipt/expense
    files in a folder. Read-only -- runs AI extraction and writes the plan to
    _reports/, but does not rename anything. Use the returned report_path with
    POST /bot/receipts/rename-apply after the user confirms.

    Query params:
      folder -- absolute folder path to scan (required)
    """
    folder_raw = (request.args.get("folder") or "").strip().strip('"')
    if not folder_raw:
        return jsonify({"error": "folder is required"}), 400

    folder = Path(folder_raw)
    if not folder.is_dir():
        return jsonify({"error": f"folder not found: {folder_raw}"}), 400

    plan, skipped = _build_rename_plan(folder)
    if not plan:
        return jsonify({
            "count": 0, "plan": [],
            "skipped_count": len(skipped),
            "skipped": skipped,
            "message": "no supported files (.pdf/.jpg/.jpeg/.png) found",
        }), 200

    report_path = receipt_rename.write_report(plan)
    review_count = sum(1 for e in plan if e["status"] == "review")
    unread_count = sum(1 for e in plan if e["status"] == "unread")

    return jsonify({
        "folder": str(folder),
        "count": len(plan),
        "review_count": review_count,
        "unread_count": unread_count,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "plan": plan,
        "report_path": str(report_path),
    }), 200


@bot_bp.route("/bot/receipts/rename-apply", methods=["POST"])
def receipts_rename_apply():
    """
    Apply a previously-generated rename plan exactly as previewed (does not
    re-run AI extraction, so the result always matches what the user
    confirmed). Caller must have already shown the preview
    (GET /bot/receipts/rename-preview) and gotten explicit confirmation.

    Body (JSON):
      report_path -- report_path returned by the preview call (required)
      actor        -- who/which account triggered this, for traceability (required)
    """
    body = request.get_json(silent=True) or {}
    report_path_raw = (body.get("report_path") or "").strip()
    actor = (body.get("actor") or "").strip()

    if not report_path_raw:
        return jsonify({"error": "report_path is required"}), 400
    if not actor:
        return jsonify({"error": "actor is required"}), 400

    report_path = Path(report_path_raw)
    reports_dir = receipt_rename._REPORTS_DIR.resolve()
    if (
        report_path.resolve().parent != reports_dir
        or not report_path.name.startswith("receipt_rename_plan_")
        or report_path.suffix != ".json"
    ):
        return jsonify({"error": "report_path must be a path returned by the preview call"}), 400
    if not report_path.is_file():
        return jsonify({"error": f"report not found: {report_path_raw}"}), 400

    try:
        plan = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"error": f"could not read report: {exc}"}), 500

    renamed = failed = 0
    for entry in plan:
        old_path = Path(entry["old_path"])
        new_path = Path(entry["new_path"])
        try:
            old_path.rename(new_path)
            renamed += 1
        except Exception as exc:
            entry["error"] = str(exc)
            failed += 1

    logger.info(
        "Bot-triggered receipt rename apply: report=%s actor=%s renamed=%d failed=%d",
        report_path, actor, renamed, failed,
    )

    return jsonify({"renamed": renamed, "failed": failed}), 200
