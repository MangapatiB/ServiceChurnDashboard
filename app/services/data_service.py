from collections import Counter, defaultdict
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from datetime import datetime
import logging
from typing import Any

from app.services.dashboard_sql_client import DashboardSqlClient
from app.services.middleware_cache import MiddlewareDataCache
from app.services.mock_data import build_mock_snapshot, get_mock_locations
from app.services.query_builders import (
    normalize_customer_segment,
    normalize_limit,
    sanitize_location,
)


logger = logging.getLogger(__name__)


class DashboardDataService:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.client = DashboardSqlClient(config)
        self.middleware_cache = MiddlewareDataCache(config)

    def open_query_session(self):
        return self.client.open_session()

    def get_dashboard_snapshot(
        self,
        location: str = "",
        limit: int | None = None,
        customer_segment: str = "res",
        query_session=None,
    ) -> dict[str, Any]:
        mode = self.config.get("DATA_SOURCE_MODE", "mock")
        safe_limit = normalize_limit(limit, default=self.config.get("HIGH_RISK_LIMIT", 12))
        safe_location = sanitize_location(location)
        safe_segment = normalize_customer_segment(customer_segment)

        logger.info(
            "Building dashboard snapshot. mode=%s location=%s limit=%s segment=%s",
            mode,
            safe_location or "ALL LOCATIONS",
            safe_limit,
            safe_segment,
        )

        if mode != "live":
            snapshot = build_mock_snapshot()
            snapshot["meta"]["location"] = safe_location or "ALL LOCATIONS"
            snapshot["meta"]["limit"] = safe_limit
            snapshot["meta"]["customer_segment"] = safe_segment
            snapshot["high_risk_customers"] = snapshot["high_risk_customers"][:safe_limit]
            logger.info("Returning mock dashboard snapshot.")
            return snapshot

        try:
            session_context = self.client.open_session() if query_session is None else nullcontext(query_session)
            with session_context as session:
                truckroll_rows = self.client.fetch_truckroll_rows(safe_location, safe_limit, query_session=session)
                subscriber_account_numbers = [row[1] for row in truckroll_rows if len(row) > 1]
                churn_rows = self.client.fetch_churn_rows(subscriber_account_numbers, safe_segment, query_session=session)
                displayed_account_numbers = self._select_displayed_account_numbers(truckroll_rows, churn_rows, safe_limit)
                displayed_subscriber_accounts = self._select_displayed_subscriber_account_numbers(
                    truckroll_rows,
                    churn_rows,
                    safe_limit,
                )
                call_rows, call_scope = self._load_call_rows(
                    displayed_subscriber_accounts,
                    safe_segment,
                    query_session=session,
                )
                return self._build_live_snapshot(
                    truckroll_rows,
                    churn_rows,
                    call_rows,
                    safe_location,
                    safe_limit,
                    call_scope,
                    safe_segment,
                    query_session=session,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to build live dashboard snapshot. Falling back to mock data. location=%s limit=%s segment=%s",
                safe_location or "ALL LOCATIONS",
                safe_limit,
                safe_segment,
            )
            snapshot = build_mock_snapshot()
            snapshot["meta"]["source"] = "fallback"
            snapshot["meta"]["status"] = "degraded"
            snapshot["meta"]["message"] = f"Live dashboard table query failed: {exc}"
            snapshot["meta"]["location"] = safe_location or "ALL LOCATIONS"
            snapshot["meta"]["limit"] = safe_limit
            snapshot["meta"]["customer_segment"] = safe_segment
            snapshot["high_risk_customers"] = snapshot["high_risk_customers"][:safe_limit]
            return snapshot

    def get_location_options(self, query_session=None) -> list[str]:
        mode = self.config.get("DATA_SOURCE_MODE", "mock")
        if mode != "live":
            return get_mock_locations()

        try:
            locations = sorted(self.client.fetch_location_options(query_session=query_session))
            return locations or get_mock_locations()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load live location options. Falling back to mock locations.")
            return get_mock_locations()

    def _load_call_rows(
        self,
        displayed_subscriber_account_numbers: list[str],
        customer_segment: str,
        query_session=None,
    ) -> tuple[list[Any], str]:
        account_scoped_rows = self.client.fetch_call_monthly_rows(
            displayed_subscriber_account_numbers,
            customer_segment,
            query_session=query_session,
        )
        return account_scoped_rows, "watchlist"

    def get_call_data_records(
        self,
        location: str = "",
        limit: int | None = None,
        customer_segment: str = "res",
        page: int | None = None,
        page_size: int | None = None,
        query_session=None,
    ) -> dict[str, Any]:
        mode = self.config.get("DATA_SOURCE_MODE", "mock")
        safe_limit = normalize_limit(limit, default=self.config.get("HIGH_RISK_LIMIT", 12))
        safe_location = sanitize_location(location)
        safe_segment = normalize_customer_segment(customer_segment)
        safe_page = normalize_limit(page, default=1, minimum=1, maximum=100000)
        safe_page_size = normalize_limit(page_size, default=100, minimum=1, maximum=500)

        if mode != "live":
            return {
                "meta": {
                    "source": "mock",
                    "status": "empty",
                    "location": safe_location or "ALL LOCATIONS",
                    "limit": safe_limit,
                    "customer_segment": safe_segment,
                    "page": safe_page,
                    "page_size": safe_page_size,
                    "total_records": 0,
                    "total_pages": 1,
                    "has_prev": False,
                    "has_next": False,
                    "page_row_start": 0,
                    "page_row_end": 0,
                    "message": "Detailed call records are only available in live mode.",
                },
                "rows": [],
            }

        try:
            session_context = self.client.open_session() if query_session is None else nullcontext(query_session)
            with session_context as session:
                truckroll_rows = self.client.fetch_truckroll_rows(safe_location, safe_limit, query_session=session)
                subscriber_account_numbers = [row[1] for row in truckroll_rows if len(row) > 1]
                # For call-data pagination, rely on truckroll watchlist order directly to avoid
                # the extra churn-table query that adds significant latency at high limits.
                displayed_account_numbers = self._dedupe_preserving_order(subscriber_account_numbers)
                total_records = len(displayed_account_numbers)
                total_pages = max((total_records + safe_page_size - 1) // safe_page_size, 1)
                effective_page = min(safe_page, total_pages)
                account_start = (effective_page - 1) * safe_page_size
                account_end = account_start + safe_page_size
                paged_account_numbers = displayed_account_numbers[account_start:account_end]
                call_record_rows = self.client.fetch_call_record_rows(
                    paged_account_numbers,
                    safe_segment,
                    query_session=session,
                )
            page_row_start = ((effective_page - 1) * safe_page_size + 1) if total_records else 0
            page_row_end = min(effective_page * safe_page_size, total_records) if total_records else 0
            return {
                "meta": {
                    "source": "live",
                    "status": "healthy",
                    "location": safe_location or "ALL LOCATIONS",
                    "limit": safe_limit,
                    "customer_segment": safe_segment,
                    "page": effective_page,
                    "page_size": safe_page_size,
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "has_prev": effective_page > 1,
                    "has_next": effective_page < total_pages,
                    "page_row_start": page_row_start,
                    "page_row_end": page_row_end,
                    "message": "Detailed live call records from dashboard SQL tables for the displayed watchlist accounts.",
                },
                "rows": [self._coerce_call_record_row(row) for row in call_record_rows],
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to load live call data records. location=%s limit=%s segment=%s",
                safe_location or "ALL LOCATIONS",
                safe_limit,
                safe_segment,
            )
            return {
                "meta": {
                    "source": "fallback",
                    "status": "degraded",
                    "location": safe_location or "ALL LOCATIONS",
                    "limit": safe_limit,
                    "customer_segment": safe_segment,
                    "page": safe_page,
                    "page_size": safe_page_size,
                    "total_records": 0,
                    "total_pages": 1,
                    "has_prev": False,
                    "has_next": False,
                    "page_row_start": 0,
                    "page_row_end": 0,
                    "message": f"Live call data records table query failed: {exc}",
                },
                "rows": [],
            }

    @staticmethod
    def _dedupe_preserving_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered_values: list[str] = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            ordered_values.append(value)
        return ordered_values

    def _build_live_snapshot(
        self,
        truckroll_rows: list[Any],
        churn_rows: list[Any],
        call_rows: list[Any],
        location: str,
        limit: int,
        call_scope: str = "watchlist",
        customer_segment: str = "res",
        query_session=None,
    ) -> dict[str, Any]:
        if not truckroll_rows:
            snapshot = build_mock_snapshot()
            snapshot["meta"]["source"] = "live"
            snapshot["meta"]["status"] = "empty"
            snapshot["meta"]["message"] = "No truckroll-flagged, contactable accounts matched the current filter."
            snapshot["meta"]["location"] = location or "ALL LOCATIONS"
            snapshot["meta"]["limit"] = limit
            snapshot["meta"]["customer_segment"] = customer_segment
            snapshot["kpis"] = [
                {"label": "Flagged accounts", "value": "0", "delta": "No matches", "tone": "warning"},
                {"label": "Outreach-ready phones", "value": "0", "delta": "No matches", "tone": "warning"},
            ]
            snapshot["geo_summary"] = []
            snapshot["high_risk_customers"] = []
            snapshot["signal_mix"] = []
            return snapshot

        snapshot = build_mock_snapshot()
        snapshot["meta"]["source"] = "live"
        snapshot["meta"]["status"] = "healthy"
        snapshot["meta"]["location"] = location or "ALL LOCATIONS"
        snapshot["meta"]["limit"] = limit
        snapshot["meta"]["customer_segment"] = customer_segment
        snapshot["meta"]["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        churn_by_account = {}
        for record in churn_rows:
            if len(record) < 6:
                continue
            churn_record = self._coerce_churn_row(record)
            if churn_record["customer_id"]:
                churn_by_account[churn_record["customer_id"]] = churn_record
        call_rollups = self._build_call_account_rollups(call_rows)
        customers = []
        signal_counter: Counter[str] = Counter()
        markets: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in truckroll_rows:
            truckroll_record = self._coerce_truckroll_row(row)
            churn_record = churn_by_account.get(truckroll_record["customer_id"], {})
            features = [feature for feature in churn_record.get("features", []) if feature]
            for feature in features:
                signal_counter[feature] += 1

            churn_probability = float(churn_record.get("risk_score", 0))
            risk_score = int(round(churn_probability))
            customer = {
                "legacy_account_number": truckroll_record["legacy_account_number"],
                "customer_id": truckroll_record["customer_id"],
                "geo": truckroll_record["geo"],
                "churn_probability": round(churn_probability, 1),
                "risk_score": risk_score,
                "drivers": ", ".join(features) if features else "Truck roll flagged",
                "last_event": "Truck roll prediction triggered",
                "next_action": self._recommend_action(risk_score),
                "phone_number": truckroll_record["phone_number"],
                "modem_mac": "",
                "modem_ip": "",
                "modem_last_seen": "",
                "modem_usint": "",
                "modem_status": "Unavailable",
                "modem_state": "Unavailable",
                "modem_usrxlvl": "",
                "modem_ustxpwr": "",
                "modem_usrxsnr": "",
                "modem_dsrxlvl": "",
                "modem_dsrxsnr": "",
                "modem_dsprefec": "",
                "modem_dspostfec": "",
                "modem_dsbw": "",
                "modem_usbw": "",
                "fiber_node": "",
                "cmts": "",
                "calls_6m": 0,
                "calls_12m": 0,
                "repeat_calls_12m": False,
                "triple_calls_12m": False,
                "modem_health": {},
            }
            call_rollup = call_rollups.get(truckroll_record["customer_id"], {})
            customer["calls_6m"] = int(call_rollup.get("calls_6m", 0))
            customer["calls_12m"] = int(call_rollup.get("calls_12m", 0))
            customer["repeat_calls_12m"] = customer["calls_12m"] >= 2
            customer["triple_calls_12m"] = customer["calls_12m"] >= 3
            customers.append(customer)
            markets[truckroll_record["geo"]].append({**customer, "features": features})

        customers.sort(key=lambda item: item["risk_score"], reverse=True)
        customers = customers[:limit]
        modem_metrics = self._enrich_customers_with_modem_health(customers, query_session=query_session)

        high_risk_count = sum(1 for item in customers if item["risk_score"] >= 90)
        avg_risk = round(sum(item["risk_score"] for item in customers) / len(customers)) if customers else 0
        signal_mix = self._build_signal_mix(signal_counter)
        geo_summary = self._build_geo_summary(markets)

        snapshot["meta"]["message"] = (
            f"Live watchlist built from ServiceChurnDashboard SQL tables for {snapshot['meta']['location']}."
        )
        snapshot["kpis"] = [
            {"label": "Flagged accounts", "value": f"{len(customers)}", "delta": f"Limit {limit}", "tone": "risk"},
            {"label": "Average churn risk", "value": f"{avg_risk}", "delta": "Latest model score", "tone": "warning"},
            {"label": "90+ risk accounts", "value": f"{high_risk_count}", "delta": "Immediate outreach", "tone": "risk"},
            {
                "label": "Outreach-ready phones",
                "value": f"{sum(1 for item in customers if item['phone_number'])}",
                "delta": "Opt-in and contactable",
                "tone": "good",
            },
            {"label": "Markets impacted", "value": f"{len(geo_summary)}", "delta": "Distinct billing cities", "tone": "warning"},
        ]
        snapshot["signal_mix"] = signal_mix
        snapshot["geo_summary"] = geo_summary
        snapshot["high_risk_customers"] = customers
        snapshot["modem_health"] = self._build_modem_health_snapshot(customers, modem_metrics)
        snapshot["call_history"] = self._build_call_history(call_rows, call_scope)
        snapshot["playbooks"] = self._build_playbooks(high_risk_count, len(customers))
        return snapshot

    def _enrich_customers_with_modem_health(self, customers: list[dict[str, Any]], query_session=None) -> dict[str, Any]:
        account_numbers = []
        for customer in customers:
            account_numbers.extend(
                account_number
                for account_number in (customer.get("customer_id"), customer.get("legacy_account_number"))
                if account_number
            )

        if not account_numbers:
            return {"telemetry_accounts": 0, "latest_seen": ""}

        try:
            modem_health_by_account = self.middleware_cache.get_modem_health_by_account(
                account_numbers,
                query_session=query_session,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load modem health data. Continuing without modem enrichment.")
            return {"telemetry_accounts": 0, "latest_seen": ""}

        telemetry_accounts = 0
        latest_seen = ""
        for customer in customers:
            modem_row = None
            for account_number in (customer.get("customer_id"), customer.get("legacy_account_number")):
                if account_number and account_number in modem_health_by_account:
                    modem_row = modem_health_by_account[account_number]
                    break
            if not modem_row:
                continue

            telemetry_accounts += 1
            last_seen = str(modem_row.get("tstamp") or "")
            latest_seen = max(latest_seen, last_seen)
            customer["modem_mac"] = str(modem_row.get("mac") or modem_row.get("imac") or modem_row.get("cmac") or "")
            customer["modem_ip"] = str(modem_row.get("ip") or "")
            customer["modem_last_seen"] = last_seen
            customer["modem_usint"] = self._normalize_account_number(modem_row.get("usint"))
            customer["modem_status"] = self._derive_modem_status(modem_row)
            customer["modem_state"] = self._stringify_metric(modem_row.get("state") or customer["modem_status"])
            customer["modem_usrxlvl"] = self._stringify_metric(modem_row.get("usrxlvl"))
            customer["modem_ustxpwr"] = self._stringify_metric(modem_row.get("ustxpwr"))
            customer["modem_usrxsnr"] = self._stringify_metric(modem_row.get("usrxsnr"))
            customer["modem_dsrxlvl"] = self._stringify_metric(modem_row.get("dsrxlvl"))
            customer["modem_dsrxsnr"] = self._stringify_metric(modem_row.get("dsrxsnr"))
            customer["modem_dsprefec"] = self._stringify_metric(modem_row.get("dsprefec"))
            customer["modem_dspostfec"] = self._stringify_metric(modem_row.get("dspostfec"))
            customer["modem_dsbw"] = self._stringify_metric(modem_row.get("dsbw"))
            customer["modem_usbw"] = self._stringify_metric(modem_row.get("usbw"))
            customer["fiber_node"] = self._stringify_metric(modem_row.get("fibernode"))
            customer["cmts"] = self._stringify_metric(modem_row.get("cmts"))
            customer["modem_health"] = modem_row

        return {"telemetry_accounts": telemetry_accounts, "latest_seen": latest_seen}

    def _build_modem_health_snapshot(self, customers: list[dict[str, Any]], modem_metrics: dict[str, Any]) -> dict[str, Any]:
        modem_rows = [customer for customer in customers if customer.get("modem_mac")]
        latest_seen = modem_metrics.get("latest_seen") or "n/a"
        connected_count = sum(1 for customer in modem_rows if customer.get("modem_ip"))

        return {
            "summary": [
                {
                    "label": "Accounts with telemetry",
                    "value": f"{modem_metrics.get('telemetry_accounts', 0)}",
                    "delta": f"{len(customers)} watchlist accounts evaluated",
                    "tone": "good" if modem_metrics.get("telemetry_accounts", 0) else "warning",
                },
                {
                    "label": "Connected modems",
                    "value": f"{connected_count}",
                    "delta": "Latest SQL Server sample with valid IP",
                    "tone": "good" if connected_count else "warning",
                },
                {
                    "label": "Latest modem sample",
                    "value": latest_seen,
                    "delta": "Refreshed in middleware every 60 minutes",
                    "tone": "warning",
                },
            ],
            "modems": [
                {
                    "geo": customer.get("geo", "UNKNOWN"),
                    "customer_id": customer.get("customer_id", ""),
                    "modem_mac": customer.get("modem_mac", ""),
                    "ip": customer.get("modem_ip", ""),
                    "last_seen": customer.get("modem_last_seen", ""),
                    "usint": customer.get("modem_usint", ""),
                    "status": customer.get("modem_status", "Unavailable"),
                    "state": customer.get("modem_state", "Unavailable"),
                    "usrxlvl": customer.get("modem_usrxlvl", ""),
                    "ustxpwr": customer.get("modem_ustxpwr", ""),
                    "usrxsnr": customer.get("modem_usrxsnr", ""),
                    "dsrxlvl": customer.get("modem_dsrxlvl", ""),
                    "dsrxsnr": customer.get("modem_dsrxsnr", ""),
                    "dsprefec": customer.get("modem_dsprefec", ""),
                    "dspostfec": customer.get("modem_dspostfec", ""),
                    "dsbw": customer.get("modem_dsbw", ""),
                    "usbw": customer.get("modem_usbw", ""),
                    "fiber_node": customer.get("fiber_node", ""),
                    "cmts": customer.get("cmts", ""),
                }
                for customer in modem_rows
            ],
        }

    @staticmethod
    def _derive_modem_status(modem_row: dict[str, Any]) -> str:
        if not modem_row:
            return "Unavailable"
        modem_state = str(modem_row.get("state") or "").strip().lower()
        if modem_state in {"online", "offline"}:
            return modem_state.title()
        modem_ip = str(modem_row.get("ip") or "").strip()
        if not modem_ip or modem_ip == "0.0.0.0":
            return "Offline"
        return "Online"

    @staticmethod
    def _stringify_metric(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _normalize_account_number(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            numeric_value = Decimal(text)
        except InvalidOperation:
            return text
        if numeric_value == numeric_value.to_integral_value():
            return str(numeric_value.quantize(Decimal("1")))
        return text

    @staticmethod
    def _coerce_truckroll_row(row: Any) -> dict[str, Any]:
        return {
            "legacy_account_number": DashboardDataService._normalize_account_number(row[0]) if len(row) > 0 else "",
            "customer_id": DashboardDataService._normalize_account_number(row[1]) if len(row) > 1 else "",
            "phone_number": str(row[2]) if len(row) > 2 and row[2] is not None else "",
            "geo": str(row[3]) if len(row) > 3 and row[3] is not None else "UNKNOWN",
        }

    @staticmethod
    def _coerce_churn_row(row: Any) -> dict[str, Any]:
        return {
            "customer_id": DashboardDataService._normalize_account_number(row[0]),
            "risk_score": float(row[1]) if len(row) > 1 and row[1] is not None else 0,
            "prediction_month": str(row[2]) if len(row) > 2 and row[2] is not None else "",
            "features": [
                str(row[3]) if len(row) > 3 and row[3] is not None else "",
                str(row[4]) if len(row) > 4 and row[4] is not None else "",
                str(row[5]) if len(row) > 5 and row[5] is not None else "",
            ],
        }

    @classmethod
    def _select_displayed_account_numbers(
        cls,
        truckroll_rows: list[Any],
        churn_rows: list[Any],
        limit: int,
    ) -> list[str]:
        churn_by_account = {}
        for record in churn_rows:
            if len(record) < 6:
                continue
            churn_record = cls._coerce_churn_row(record)
            if churn_record["customer_id"]:
                churn_by_account[churn_record["customer_id"]] = churn_record

        customers = []
        for row in truckroll_rows:
            truckroll_record = cls._coerce_truckroll_row(row)
            churn_record = churn_by_account.get(truckroll_record["customer_id"], {})
            churn_probability = float(churn_record.get("risk_score", 0))
            customers.append(
                {
                    "legacy_account_number": truckroll_record["legacy_account_number"],
                    "customer_id": truckroll_record["customer_id"],
                    "risk_score": int(round(churn_probability)),
                }
            )

        customers.sort(key=lambda item: item["risk_score"], reverse=True)
        selected_accounts = []
        seen_accounts = set()
        for customer in customers[:limit]:
            for account_key in (customer.get("customer_id", ""), customer.get("legacy_account_number", "")):
                if not account_key or account_key in seen_accounts:
                    continue
                seen_accounts.add(account_key)
                selected_accounts.append(account_key)
        return selected_accounts

    @classmethod
    def _select_displayed_subscriber_account_numbers(
        cls,
        truckroll_rows: list[Any],
        churn_rows: list[Any],
        limit: int,
    ) -> list[str]:
        churn_by_account = {}
        for record in churn_rows:
            if len(record) < 6:
                continue
            churn_record = cls._coerce_churn_row(record)
            if churn_record["customer_id"]:
                churn_by_account[churn_record["customer_id"]] = churn_record

        customers = []
        for row in truckroll_rows:
            truckroll_record = cls._coerce_truckroll_row(row)
            churn_record = churn_by_account.get(truckroll_record["customer_id"], {})
            churn_probability = float(churn_record.get("risk_score", 0))
            customers.append(
                {
                    "customer_id": truckroll_record["customer_id"],
                    "risk_score": int(round(churn_probability)),
                }
            )

        customers.sort(key=lambda item: item["risk_score"], reverse=True)
        selected_accounts = []
        seen_accounts = set()
        for customer in customers[:limit]:
            customer_id = customer.get("customer_id", "")
            if not customer_id or customer_id in seen_accounts:
                continue
            seen_accounts.add(customer_id)
            selected_accounts.append(customer_id)
        return selected_accounts

    @staticmethod
    def _coerce_call_row(row: Any) -> dict[str, Any]:
        return {
            "number_of_calls": int(row[0]) if len(row) > 0 and row[0] is not None else 0,
            "account_number": DashboardDataService._normalize_account_number(row[1]) if len(row) > 1 else "",
            "month_start": DashboardDataService._coerce_month_value(row[2]) if len(row) > 2 else None,
            "contact_month_start": DashboardDataService._coerce_month_value(row[3]) if len(row) > 3 else None,
            "average_agent_talk_minutes": float(row[4]) if len(row) > 4 and row[4] is not None else 0,
            "average_total_contact_duration_minutes": float(row[5]) if len(row) > 5 and row[5] is not None else 0,
            "total_agent_talk_minutes": float(row[6]) if len(row) > 6 and row[6] is not None else 0,
            "total_contact_duration_minutes": float(row[7]) if len(row) > 7 and row[7] is not None else 0,
        }

    @staticmethod
    def _coerce_call_record_row(row: Any) -> dict[str, Any]:
        month_start = DashboardDataService._coerce_month_value(row[3]) if len(row) > 3 else None
        resolved_value = False
        if len(row) > 8 and row[8] is not None:
            if isinstance(row[8], bool):
                resolved_value = row[8]
            else:
                resolved_value = str(row[8]).strip().lower() in {"true", "1", "yes", "y"}
        return {
            "customer_account": DashboardDataService._normalize_account_number(row[0]) if len(row) > 0 else "",
            "subscriber_account": DashboardDataService._normalize_account_number(row[1]) if len(row) > 1 else "",
            "customer_type": str(row[2]) if len(row) > 2 and row[2] is not None else "",
            "month_start": month_start.strftime("%Y-%m") if month_start is not None else "",
            "number_of_calls": int(row[4]) if len(row) > 4 and row[4] is not None else 0,
            "total_duration_minutes": float(row[5]) if len(row) > 5 and row[5] is not None else 0,
            "avg_duration_minutes": float(row[6]) if len(row) > 6 and row[6] is not None else 0,
            "client_sentiment": str(row[7]).upper() if len(row) > 7 and row[7] is not None else "UNKNOWN",
            "is_resolved": resolved_value,
        }

    @staticmethod
    def _coerce_month_value(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            return datetime(value.year, value.month, value.day)
        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None

    def _build_call_account_rollups(self, call_rows: list[Any]) -> dict[str, dict[str, float]]:
        if not call_rows:
            return {}

        raw_call_rows = []
        for row in call_rows:
            call_record = self._coerce_call_row(row)
            if not call_record["account_number"] or not call_record["month_start"]:
                continue
            raw_call_rows.append(call_record)

        if not raw_call_rows:
            return {}

        latest_month = max(row["month_start"] for row in raw_call_rows if row["month_start"] is not None)
        account_rollups: dict[str, dict[str, float]] = {}

        for row in raw_call_rows:
            month_start = row["month_start"]
            if month_start is None:
                continue
            months_difference = ((latest_month.year - month_start.year) * 12) + (latest_month.month - month_start.month)
            if months_difference < 0:
                continue

            rollup = account_rollups.setdefault(
                row["account_number"],
                {
                    "calls_6m": 0,
                    "calls_12m": 0,
                    "duration_6m": 0.0,
                    "duration_12m": 0.0,
                },
            )

            if months_difference <= 5:
                rollup["calls_6m"] += row["number_of_calls"]
                rollup["duration_6m"] += row["total_contact_duration_minutes"]
            if months_difference <= 11:
                rollup["calls_12m"] += row["number_of_calls"]
                rollup["duration_12m"] += row["total_contact_duration_minutes"]

        return account_rollups

    def _build_call_history(self, call_rows: list[Any], call_scope: str = "watchlist") -> dict[str, Any]:
        account_rollups = self._build_call_account_rollups(call_rows)

        if not account_rollups:
            return {"summary": [], "segments": [], "scope": call_scope}

        summary_row = {
            "watchlist_accounts": len(account_rollups),
            "repeat_6m_accounts": sum(1 for row in account_rollups.values() if row["calls_6m"] >= 2),
            "repeat_12m_accounts": sum(1 for row in account_rollups.values() if row["calls_12m"] >= 2),
            "triple_call_accounts": sum(1 for row in account_rollups.values() if row["calls_12m"] >= 3),
            "repeat_6m_call_volume": sum(row["calls_6m"] for row in account_rollups.values() if row["calls_6m"] >= 2),
            "repeat_12m_call_volume": sum(row["calls_12m"] for row in account_rollups.values() if row["calls_12m"] >= 2),
            "triple_call_duration": round(
                sum(row["duration_12m"] for row in account_rollups.values() if row["calls_12m"] >= 3),
                1,
            ),
            "one_call_12m_accounts": sum(1 for row in account_rollups.values() if row["calls_12m"] == 1),
            "two_call_12m_accounts": sum(1 for row in account_rollups.values() if row["calls_12m"] == 2),
            "three_plus_12m_accounts": sum(1 for row in account_rollups.values() if row["calls_12m"] >= 3),
        }

        watchlist_size = max(1, summary_row["watchlist_accounts"])

        def percentage(count: int) -> int:
            return round((count / watchlist_size) * 100)

        return {
            "scope": call_scope,
            "summary": [
                {
                    "label": "6m repeat callers",
                    "value": f"{summary_row['repeat_6m_accounts']:,}",
                    "delta": f"{summary_row['repeat_6m_call_volume']:,} calls in 6 months",
                    "tone": "warning",
                },
                {
                    "label": "12m repeat callers",
                    "value": f"{summary_row['repeat_12m_accounts']:,}",
                    "delta": f"{summary_row['repeat_12m_call_volume']:,} calls in 12 months",
                    "tone": "risk",
                },
                {
                    "label": "3x+ call accounts",
                    "value": f"{summary_row['triple_call_accounts']:,}",
                    "delta": f"{summary_row['triple_call_duration']:.1f} min total duration",
                    "tone": "risk",
                },
            ],
            "segments": [
                {
                    "label": "1 call in 6-12 months",
                    "value": percentage(summary_row['one_call_12m_accounts']),
                    "detail": f"{summary_row['one_call_12m_accounts']} accounts had exactly one authenticated call in the last 12 months.",
                },
                {
                    "label": "2 calls in 6-12 months",
                    "value": percentage(summary_row['two_call_12m_accounts']),
                    "detail": f"{summary_row['two_call_12m_accounts']} accounts logged exactly two authenticated calls in the last 12 months.",
                },
                {
                    "label": "3+ calls in 6-12 months",
                    "value": percentage(summary_row['three_plus_12m_accounts']),
                    "detail": f"{summary_row['three_plus_12m_accounts']} accounts logged three or more authenticated calls in the last 12 months.",
                },
            ],
        }

    @staticmethod
    def _recommend_action(risk_score: int) -> str:
        if risk_score >= 90:
            return "Immediate call and SMS outreach"
        if risk_score >= 75:
            return "Queue for proactive retention contact"
        if risk_score >= 60:
            return "Monitor and prepare message"
        return "Monitor"

    @staticmethod
    def _risk_tier(heuristic_score: int) -> str:
        if heuristic_score >= 75:
            return "Tier 1"
        if heuristic_score >= 50:
            return "Tier 2"
        return "Tier 3"

    @staticmethod
    def _geo_recommended_action(risk_tier: str) -> str:
        if risk_tier == "Tier 1":
            return "Immediate retention and operations response"
        if risk_tier == "Tier 2":
            return "Prioritize proactive outreach and service follow-up"
        return "Monitor trend and prepare targeted campaign"

    @staticmethod
    def _build_signal_mix(signal_counter: Counter[str]) -> list[dict[str, Any]]:
        if not signal_counter:
            return []

        total = sum(signal_counter.values())
        return [
            {"label": label, "value": round((count / total) * 100)}
            for label, count in signal_counter.most_common(5)
        ]

    def _build_geo_summary(self, markets: defaultdict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        geo_summary = []
        max_flagged_accounts = max((len(entries) for entries in markets.values()), default=0)
        for geo, entries in sorted(markets.items(), key=lambda item: len(item[1]), reverse=True):
            avg_risk = round(sum(entry["risk_score"] for entry in entries) / len(entries)) if entries else 0
            feature_counter = Counter(feature for entry in entries for feature in entry.get("features", []) if feature)
            flagged_accounts = len(entries)
            high_risk_count = sum(1 for entry in entries if entry["risk_score"] >= 90)
            repeat_call_accounts = sum(1 for entry in entries if entry.get("repeat_calls_12m"))
            triple_call_accounts = sum(1 for entry in entries if entry.get("triple_calls_12m"))
            high_risk_rate = round((high_risk_count / flagged_accounts) * 100) if flagged_accounts else 0
            repeat_call_rate = round((repeat_call_accounts / flagged_accounts) * 100) if flagged_accounts else 0
            triple_call_rate = round((triple_call_accounts / flagged_accounts) * 100) if flagged_accounts else 0
            truckroll_pressure = round((flagged_accounts / max_flagged_accounts) * 100) if max_flagged_accounts else 0
            heuristic_score = round(
                (avg_risk * 0.5)
                + (high_risk_rate * 0.25)
                + (repeat_call_rate * 0.15)
                + (triple_call_rate * 0.05)
                + (truckroll_pressure * 0.05)
            )
            risk_tier = self._risk_tier(heuristic_score)
            geo_summary.append(
                {
                    "geo": geo,
                    "flagged_accounts": flagged_accounts,
                    "avg_risk": avg_risk,
                    "high_risk_count": high_risk_count,
                    "contactable_count": sum(1 for entry in entries if entry["phone_number"]),
                    "top_driver": feature_counter.most_common(1)[0][0] if feature_counter else "Truck roll flagged",
                    "risk_tier": risk_tier,
                    "recommended_action": self._geo_recommended_action(risk_tier),
                    "heuristic_score": heuristic_score,
                    "high_risk_rate": high_risk_rate,
                    "repeat_call_rate": repeat_call_rate,
                    "triple_call_rate": triple_call_rate,
                    "truckroll_pressure": truckroll_pressure,
                }
            )
        return geo_summary

    @staticmethod
    def _build_playbooks(high_risk_count: int, total_customers: int) -> list[dict[str, Any]]:
        return [
            {
                "tier": "Tier 1",
                "title": "Immediate contact queue",
                "detail": f"{high_risk_count} accounts are at 90+ churn risk and should move to same-day outbound call and SMS outreach.",
            },
            {
                "tier": "Tier 2",
                "title": "Retention worklist",
                "detail": "Customers with elevated churn drivers but lower urgency should be queued for proactive contact and service follow-up.",
            },
            {
                "tier": "Tier 3",
                "title": "Market monitoring",
                "detail": f"Track signal concentration across {total_customers} flagged accounts to decide where operations and marketing should intervene next.",
            },
        ]
