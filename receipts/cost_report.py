import json
from datetime import datetime
from pathlib import Path


_AI_LOG_PATH = Path(__file__).resolve().parent.parent / "_lib" / "data" / "logs" / "ai_client.json"


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _load_calls(ai_log_path: Path) -> list[dict]:
    try:
        data = json.loads(ai_log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    calls = data.get("calls", [])
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _empty_summary(since_iso: str) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "since": since_iso,
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0,
        "by_model": [],
        "notes": [
            "Estimate only. Final provider billing may differ.",
            "Calls without token usage are counted but cannot be priced.",
        ],
    }


def build_cost_summary(since_iso: str, ai_log_path: Path | None = None) -> dict:
    ai_log_path = ai_log_path or _AI_LOG_PATH
    since = _parse_ts(since_iso)
    summary = _empty_summary(since_iso)
    if since is None:
        summary["notes"].append("Invalid run start timestamp; no calls were matched.")
        return summary

    by_model: dict[tuple[str, str], dict] = {}
    for call in _load_calls(ai_log_path):
        timestamp = _parse_ts(call.get("timestamp"))
        if timestamp is None or timestamp < since:
            continue
        if call.get("event") != "ai_call":
            continue

        provider = call.get("provider") or "unknown"
        model = call.get("model") or "unknown"
        key = (provider, model)
        bucket = by_model.setdefault(
            key,
            {
                "provider": provider,
                "model": model,
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0,
                "unpriced_call_count": 0,
                "pricing_source": "",
            },
        )

        usage = call.get("usage") or {}
        cost = call.get("cost") or {}
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        total_tokens = usage.get("total_tokens") or input_tokens + output_tokens
        estimated_cost = cost.get("estimated_cost_usd")

        bucket["call_count"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        if estimated_cost is None:
            bucket["unpriced_call_count"] += 1
        else:
            bucket["estimated_cost_usd"] += estimated_cost
        if cost.get("pricing_source"):
            bucket["pricing_source"] = cost["pricing_source"]

    for bucket in by_model.values():
        bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"], 6)
        summary["call_count"] += bucket["call_count"]
        summary["input_tokens"] += bucket["input_tokens"]
        summary["output_tokens"] += bucket["output_tokens"]
        summary["total_tokens"] += bucket["total_tokens"]
        summary["estimated_cost_usd"] += bucket["estimated_cost_usd"]

    summary["estimated_cost_usd"] = round(summary["estimated_cost_usd"], 6)
    summary["by_model"] = sorted(
        by_model.values(),
        key=lambda item: (item["provider"], item["model"]),
    )
    return summary


def _format_text(summary: dict) -> str:
    lines = [
        "AI Cost Estimate",
        "================",
        f"Generated at: {summary['generated_at']}",
        f"Run started:  {summary['since']}",
        "",
        f"AI calls:      {summary['call_count']}",
        f"Input tokens:  {summary['input_tokens']}",
        f"Output tokens: {summary['output_tokens']}",
        f"Total tokens:  {summary['total_tokens']}",
        f"Estimated USD: ${summary['estimated_cost_usd']:.6f}",
        "",
    ]
    for item in summary["by_model"]:
        lines.extend(
            [
                f"{item['provider']} / {item['model']}",
                f"  calls:      {item['call_count']}",
                f"  input:      {item['input_tokens']}",
                f"  output:     {item['output_tokens']}",
                f"  total:      {item['total_tokens']}",
                f"  estimated:  ${item['estimated_cost_usd']:.6f}",
                f"  unpriced:   {item['unpriced_call_count']}",
                f"  pricing:    {item['pricing_source'] or 'not configured'}",
                "",
            ]
        )
    lines.extend(summary["notes"])
    return "\n".join(lines) + "\n"


def write_cost_report(output_dir: Path, since_iso: str, ai_log_path: Path | None = None) -> tuple[Path, Path]:
    summary = build_cost_summary(since_iso, ai_log_path=ai_log_path)
    json_path = output_dir / "ai_cost_report.json"
    txt_path = output_dir / "ai_cost_report.txt"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(_format_text(summary), encoding="utf-8")
    return json_path, txt_path
