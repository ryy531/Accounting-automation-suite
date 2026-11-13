"""
connectors/nocodb_client.py -- NocoDB REST API client.

Credentials: read from _lib/config/settings.py (.env file).
Minimal implementation for payroll prepare functionality.
"""

import logging
import time
from urllib.parse import quote

import requests

from config.settings import NOCODB_URL, NOCODB_TOKEN

logger = logging.getLogger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────

class NocoDBError(Exception):
    """Base exception for NocoDB client errors."""


class NocoDBAuthError(NocoDBError):
    """Authentication failure (invalid or missing token)."""


class NocoDBApiError(NocoDBError):
    """API call failed (HTTP 4xx/5xx or connection error)."""


# ── Client ───────────────────────────────────────────────────────────────────

class NocoDBClient:
    """
    Lightweight REST client for NocoDB.

    Usage:
        client = NocoDBClient()
        rows = client.list_rows(table_id=123)
        client.create_row(table_id=456, fields={"Name": "Test"})
    """

    def __init__(self, timeout: int = 10):
        self._base_url = NOCODB_URL
        self._token = NOCODB_TOKEN
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "xc-token": self._token,
            "Content-Type": "application/json",
        })

        if not self._base_url or not self._token:
            logger.warning(
                "NOCODB_URL or NOCODB_TOKEN is empty — API calls will fail"
            )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _get(self, url: str):
        """Shared GET request with error handling."""
        if not self._base_url or not self._token:
            raise NocoDBAuthError(
                "NOCODB_URL or NOCODB_TOKEN is not configured in .env"
            )
        resp = None
        last_exc = None
        for attempt in range(1, 4):
            try:
                resp = self._session.get(url, timeout=self._timeout)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 3:
                    logger.warning(
                        "NocoDB GET attempt %d failed; retrying: %s",
                        attempt,
                        exc,
                    )
                    time.sleep(attempt)
                    continue
                if isinstance(exc, requests.Timeout):
                    logger.error("NocoDB request timed out: %s", exc)
                    raise NocoDBApiError(f"Timeout: {exc}") from exc
                if isinstance(exc, requests.ConnectionError):
                    logger.error("NocoDB connection failed: %s", exc)
                    raise NocoDBApiError(f"Connection error: {exc}") from exc
                logger.error("NocoDB request failed: %s", exc)
                raise NocoDBApiError(str(exc)) from exc

            is_sqlite_busy = (
                resp.status_code == 400
                and (
                    "SQLITE_BUSY" in resp.text
                    or "database is locked" in resp.text.lower()
                )
            )
            if is_sqlite_busy and attempt < 3:
                logger.warning(
                    "NocoDB database busy on GET attempt %d; retrying",
                    attempt,
                )
                time.sleep(attempt)
                continue
            break

        if resp is None:
            raise NocoDBApiError(f"NocoDB GET failed: {last_exc}")

        if resp.status_code in (401, 403):
            raise NocoDBAuthError(
                f"NocoDB auth failed ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            logger.error(
                "NocoDB API error %d: %s", resp.status_code, resp.text[:500]
            )
            raise NocoDBApiError(
                f"NocoDB API returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise NocoDBApiError(f"Invalid JSON response: {exc}") from exc

    def _post(self, url: str, data: dict):
        """Shared POST request with error handling."""
        if not self._base_url or not self._token:
            raise NocoDBAuthError(
                "NOCODB_URL or NOCODB_TOKEN is not configured in .env"
            )
        try:
            resp = self._session.post(url, json=data, timeout=self._timeout)
        except requests.ConnectionError as exc:
            logger.error("NocoDB connection failed: %s", exc)
            raise NocoDBApiError(f"Connection error: {exc}") from exc
        except requests.Timeout as exc:
            logger.error("NocoDB request timed out: %s", exc)
            raise NocoDBApiError(f"Timeout: {exc}") from exc
        except requests.RequestException as exc:
            logger.error("NocoDB request failed: %s", exc)
            raise NocoDBApiError(str(exc)) from exc

        if resp.status_code in (401, 403):
            raise NocoDBAuthError(
                f"NocoDB auth failed ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            logger.error(
                "NocoDB API error %d: %s", resp.status_code, resp.text[:500]
            )
            raise NocoDBApiError(
                f"NocoDB API returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise NocoDBApiError(f"Invalid JSON response: {exc}") from exc

    def _patch(self, url: str, data: dict):
        """Shared PATCH request with error handling."""
        if not self._base_url or not self._token:
            raise NocoDBAuthError(
                "NOCODB_URL or NOCODB_TOKEN is not configured in .env"
            )
        try:
            resp = self._session.patch(url, json=data, timeout=self._timeout)
        except requests.ConnectionError as exc:
            logger.error("NocoDB connection failed: %s", exc)
            raise NocoDBApiError(f"Connection error: {exc}") from exc
        except requests.Timeout as exc:
            logger.error("NocoDB request timed out: %s", exc)
            raise NocoDBApiError(f"Timeout: {exc}") from exc
        except requests.RequestException as exc:
            logger.error("NocoDB request failed: %s", exc)
            raise NocoDBApiError(str(exc)) from exc

        if resp.status_code in (401, 403):
            raise NocoDBAuthError(
                f"NocoDB auth failed ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            logger.error(
                "NocoDB API error %d: %s", resp.status_code, resp.text[:500]
            )
            raise NocoDBApiError(
                f"NocoDB API returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise NocoDBApiError(f"Invalid JSON response: {exc}") from exc

    # ── Public API ─────────────────────────────────────────────────────────────

    def list_rows(self, table_id: str, filters: list[tuple] | None = None,
                  order_by: str | None = None, size: int | None = None) -> dict:
        """
        List rows from a NocoDB table with pagination support.

        Args:
            table_id: NocoDB table ID (string, e.g., "muoolwcrncbs21t")
            filters: List of (field_name, operator, value) tuples.
                     Supported operators: "eq", "neq", "gt", "lt", "gte", "lte",
                     "contains", "like", "boolean" (for checkbox fields).
            order_by: Field name to sort by, prefix with "-" for descending.
            size: Maximum number of rows to return (None for all).

        Returns:
            Dict with "list" (rows) and "count" (total count).

        Note:
            Fetches all rows via pagination to ensure complete results.
            Filters are applied in Python for simplicity.
        """
        all_rows = []
        offset = 0
        limit = 100  # NocoDB default page size

        while True:
            # Build API URL with pagination
            url = f"{self._base_url}/api/v2/tables/{table_id}/records"
            params = {
                "limit": limit,
                "offset": offset,
            }
            query_parts = [f"{k}={v}" for k, v in params.items()]
            url += "?" + "&".join(query_parts)

            try:
                response = self._get(url)
            except NocoDBApiError as exc:
                # NocoDB returns 422 ERR_INVALID_OFFSET_VALUE instead of an
                # empty list when offset lands exactly on the row count
                # (happens whenever total rows is a multiple of the page size).
                # Treat that as "no more rows" rather than a real failure.
                if all_rows and "ERR_INVALID_OFFSET_VALUE" in str(exc):
                    break
                raise
            rows = response.get("list", [])

            if not rows:
                break

            all_rows.extend(rows)

            # Apply size limit if specified
            if size is not None and len(all_rows) >= size:
                all_rows = all_rows[:size]
                break

            page_info = response.get("pageInfo") or {}
            total_rows = page_info.get("totalRows")
            if page_info.get("isLastPage"):
                break
            if isinstance(total_rows, int) and offset + len(rows) >= total_rows:
                break

            # Fallback for responses without pageInfo.
            if len(rows) < limit:
                break

            offset += limit

        # Apply filters in Python
        filtered_rows = all_rows
        if filters:
            filtered_rows = []
            for row in all_rows:
                match = True
                for field_name, op, value in filters:
                    if field_name not in row:
                        match = False
                        break
                    row_value = row[field_name]
                    if op == "boolean":
                        if bool(value) != bool(row_value):
                            match = False
                            break
                    elif op == "eq":
                        if str(value) != str(row_value):
                            match = False
                            break
                    elif op == "neq":
                        if str(value) == str(row_value):
                            match = False
                            break
                    elif op == "gte":
                        if str(value) > str(row_value):
                            match = False
                            break
                    elif op == "lte":
                        if str(value) < str(row_value):
                            match = False
                            break
                    elif op == "contains":
                        if value not in str(row_value):
                            match = False
                            break
                    elif op == "like":
                        if str(value) != str(row_value):
                            match = False
                            break
                    else:
                        logger.warning("Unsupported filter operator: %s", op)
                if match:
                    filtered_rows.append(row)

        # Apply sorting in Python if needed
        if order_by:
            sort_field = order_by.lstrip("-")
            reverse = order_by.startswith("-")
            try:
                filtered_rows.sort(
                    key=lambda r: str(r.get(sort_field, "")), reverse=reverse
                )
            except (TypeError, AttributeError) as e:
                logger.warning("Failed to sort by %s: %s", sort_field, e)

        return {"list": filtered_rows, "count": len(filtered_rows)}

    def create_row(self, table_id: str, fields: dict) -> dict:
        """
        Create a new row in a NocoDB table.

        Args:
            table_id: NocoDB table ID (string)
            fields: Dict of field_name -> value

        Returns:
            Created row dict with Id and all fields.
        """
        url = f"{self._base_url}/api/v2/tables/{table_id}/records"
        return self._post(url, [fields])

    def update_row(self, table_id: str, row_id: int, fields: dict) -> dict:
        """
        Update an existing row in a NocoDB table.

        Args:
            table_id: NocoDB table ID (string)
            row_id: Row ID (integer)
            fields: Dict of field_name -> value to update

        Returns:
            Updated row dict.
        """
        url = f"{self._base_url}/api/v2/tables/{table_id}/records"
        update_data = {"Id": row_id, **fields}
        return self._patch(url, [update_data])

    def get_table_metadata(self, table_id: str) -> dict:
        """Return NocoDB metadata for one table."""
        url = f"{self._base_url}/api/v2/meta/tables/{table_id}"
        return self._get(url)

    def list_view_filters(self, view_id: str) -> list[dict]:
        """Return filter definitions for one NocoDB view."""
        url = f"{self._base_url}/api/v2/meta/views/{view_id}/filters"
        response = self._get(url)
        return response.get("list", [])

    def get_table_hooks(self, table_id: str) -> list[dict]:
        """Return webhook definitions for one NocoDB table."""
        url = f"{self._base_url}/api/v2/meta/tables/{table_id}/hooks"
        response = self._get(url)
        return response.get("list", [])

    def update_view_filter(self, filter_id: str, fields: dict) -> dict:
        """Update an existing NocoDB view filter."""
        url = f"{self._base_url}/api/v2/meta/filters/{filter_id}"
        return self._patch(url, fields)

    # ── Links (LinksToRecord / mm relation fields) ───────────────────────────
    #
    # The generic records endpoint (create_row/update_row above) does not
    # write LinksToRecord fields. NocoDB exposes a dedicated endpoint per
    # link field, confirmed by live probe against a disposable test table
    # pair (_doc/v6_verification_results_2026-06-23.md): payload is always
    # a list of {"Id": <related_row_id>} objects, for one related row or many.

    def _delete(self, url: str, data: dict):
        """Shared DELETE request with error handling."""
        if not self._base_url or not self._token:
            raise NocoDBAuthError(
                "NOCODB_URL or NOCODB_TOKEN is not configured in .env"
            )
        try:
            resp = self._session.delete(url, json=data, timeout=self._timeout)
        except requests.ConnectionError as exc:
            logger.error("NocoDB connection failed: %s", exc)
            raise NocoDBApiError(f"Connection error: {exc}") from exc
        except requests.Timeout as exc:
            logger.error("NocoDB request timed out: %s", exc)
            raise NocoDBApiError(f"Timeout: {exc}") from exc
        except requests.RequestException as exc:
            logger.error("NocoDB request failed: %s", exc)
            raise NocoDBApiError(str(exc)) from exc

        if resp.status_code in (401, 403):
            raise NocoDBAuthError(
                f"NocoDB auth failed ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            logger.error(
                "NocoDB API error %d: %s", resp.status_code, resp.text[:500]
            )
            raise NocoDBApiError(
                f"NocoDB API returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise NocoDBApiError(f"Invalid JSON response: {exc}") from exc

    def link_records(
        self, table_id: str, link_field_id: str, row_id: int, related_ids: list[int]
    ) -> bool:
        """
        Link one or more rows to `row_id` via a LinksToRecord field.

        Idempotent on the NocoDB side: relinking an already-linked row does
        not create a duplicate (confirmed by live probe).
        """
        url = f"{self._base_url}/api/v2/tables/{table_id}/links/{link_field_id}/records/{row_id}"
        return self._post(url, [{"Id": rid} for rid in related_ids])

    def unlink_records(
        self, table_id: str, link_field_id: str, row_id: int, related_ids: list[int]
    ) -> bool:
        """Remove one or more links from `row_id`. Does not delete the related rows."""
        url = f"{self._base_url}/api/v2/tables/{table_id}/links/{link_field_id}/records/{row_id}"
        return self._delete(url, [{"Id": rid} for rid in related_ids])

    def list_linked_records(
        self, table_id: str, link_field_id: str, row_id: int
    ) -> list[dict]:
        """List rows currently linked to `row_id` via a LinksToRecord field."""
        url = f"{self._base_url}/api/v2/tables/{table_id}/links/{link_field_id}/records/{row_id}"
        response = self._get(url)
        return response.get("list", [])
