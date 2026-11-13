"""
server/run_people_backfill.py -- CLI entry point for the People-Company
Owners backfill (Step 1d of the cross-validated bridging design, see
_doc/cross_validation_*_2026-06-23*.md and _doc/v6_verification_results_2026-06-23.md).

Re-runs people_bridging matching against the current Companies/People data
and, for every "link" decision, calls data_access.link_person_to_company().
Idempotent: re-running after a partial run only links what isn't already
linked (both at the NocoDB API level and via data_access's own check).

Default is dry-run (no writes). Pass --apply to actually write links.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data_access
from server.people_bridging import match_primary_owner, match_secondary_owner

logger = logging.getLogger(__name__)
_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def run_backfill(apply: bool, actor: str = "People-Company Backfill") -> dict:
    companies = data_access.list_companies()
    people = data_access.list_people()

    results = {"linked": [], "noop": [], "needs_review": [], "skipped": [], "errors": []}

    for c in companies:
        for role, matcher in (
            ("primary_owner", match_primary_owner),
            ("secondary_owner", match_secondary_owner),
        ):
            decision = matcher(c, people)
            action = decision["action"]

            if action == "skip":
                continue
            if action == "needs_review":
                results["needs_review"].append({
                    "company_id": c.get("Id"), "legal_name": c.get("Legal Name"),
                    "role": role, "reason": decision["reason"],
                })
                continue

            # action == "link"
            company_id = c.get("Id")
            person_id = decision["person_id"]
            if not apply:
                results["linked"].append({
                    "company_id": company_id, "legal_name": c.get("Legal Name"),
                    "role": role, "person_id": person_id, "reason": decision["reason"],
                    "dry_run": True,
                })
                continue
            try:
                outcome = data_access.link_person_to_company(
                    company_id, person_id, role=role, actor=actor
                )
                bucket = "linked" if outcome["action"] == "linked" else "noop"
                results[bucket].append({
                    "company_id": company_id, "legal_name": c.get("Legal Name"),
                    "role": role, "person_id": person_id, "reason": decision["reason"],
                })
                logger.info(
                    "%s: Company %s (%s) <-> Person %s [%s]",
                    outcome["action"], company_id, c.get("Legal Name"), person_id, role,
                )
            except Exception as exc:
                logger.exception(
                    "Failed to link Company %s <-> Person %s", company_id, person_id
                )
                results["errors"].append({
                    "company_id": company_id, "legal_name": c.get("Legal Name"),
                    "role": role, "person_id": person_id, "error": str(exc),
                })

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="People-Company Owners backfill (Step 1d)"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write Owners links in NocoDB (default: dry-run)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON file path (default: auto-generated in logs/)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    mode = "apply" if args.apply else "dryrun"
    logger.info("People-Company backfill starting (mode=%s)", mode)

    results = run_backfill(apply=args.apply)

    summary = {
        "mode": mode,
        "run_at": datetime.now().isoformat(),
        "counts": {k: len(v) for k, v in results.items()},
        "details": results,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = _LOG_DIR / f"people_backfill_{mode}_{timestamp}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("Counts: %s", summary["counts"])
    logger.info("Wrote summary to %s", output_path)

    return 1 if results["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
