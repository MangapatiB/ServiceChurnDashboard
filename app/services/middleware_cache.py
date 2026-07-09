from datetime import datetime, timedelta
import logging
import time
from threading import RLock
from typing import Any

from app.services.dashboard_sql_client import DashboardSqlClient, DashboardSqlQuerySession
from app.services.sql_server_client import SqlServerClient


logger = logging.getLogger(__name__)


class MiddlewareDataCache:
    def __init__(self, config: dict[str, Any]):
        self.refresh_interval = timedelta(seconds=int(config.get("MODEM_HEALTH_REFRESH_SECONDS", 3600)))
        self.retry_chunk_size = int(config.get("MODEM_ENRICH_RETRY_CHUNK_SIZE", 200))
        self.retry_max_chunks = int(config.get("MODEM_ENRICH_RETRY_MAX_CHUNKS", 15))
        self.retry_time_budget_seconds = float(config.get("MODEM_ENRICH_RETRY_TIME_BUDGET_SECONDS", 20))
        self.dashboard_sql_client = DashboardSqlClient(config)
        self._lock = RLock()
        self._account_mac_cache: dict[str, str] = {}
        self._account_mac_refreshed_at: datetime | None = None
        self._modem_health_cache: dict[str, dict[str, Any]] = {}
        self._modem_health_refreshed_at: dict[str, datetime] = {}

    @staticmethod
    def _iter_chunks(items: list[str], chunk_size: int) -> list[list[str]]:
        safe_chunk_size = max(int(chunk_size or 1), 1)
        return [items[index:index + safe_chunk_size] for index in range(0, len(items), safe_chunk_size)]

    def get_modem_health_by_account(
        self,
        account_numbers: list[str],
        query_session: DashboardSqlQuerySession | None = None,
    ) -> dict[str, dict[str, Any]]:
        sanitized_accounts = []
        seen_accounts = set()
        for account_number in account_numbers:
            normalized_account = str(account_number or "").strip()
            if not normalized_account or normalized_account in seen_accounts:
                continue
            seen_accounts.add(normalized_account)
            sanitized_accounts.append(normalized_account)

        if not sanitized_accounts:
            return {}

        account_mac_map = self._get_account_mac_map(sanitized_accounts, query_session=query_session)
        account_to_mac: dict[str, str] = {}
        for account_number in sanitized_accounts:
            modem_mac = self._normalize_mac_key(account_mac_map.get(account_number))
            if modem_mac:
                account_to_mac[account_number] = modem_mac

        if not account_to_mac:
            logger.warning(
                "No account-to-MAC mappings found for %d requested accounts. "
                "Populate service_churn_account_mac_map to enable modem enrichment.",
                len(sanitized_accounts),
            )
            return {}

        modem_health_by_mac = self._get_modem_health_rows(list(account_to_mac.values()), query_session=query_session)
        return {
            account_number: modem_health_by_mac[modem_mac]
            for account_number, modem_mac in account_to_mac.items()
            if modem_mac in modem_health_by_mac
        }

    def _get_account_mac_map(
        self,
        account_numbers: list[str],
        query_session: DashboardSqlQuerySession | None = None,
    ) -> dict[str, str]:
        with self._lock:
            if not account_numbers:
                return {}

            cache_is_fresh = self._account_mac_cache and not self._is_stale(self._account_mac_refreshed_at)
            missing_accounts = [
                account_number for account_number in account_numbers if account_number not in self._account_mac_cache
            ]
            if cache_is_fresh and not missing_accounts:
                return {
                    account_number: self._account_mac_cache[account_number]
                    for account_number in account_numbers
                    if account_number in self._account_mac_cache
                }

            if not self.dashboard_sql_client.is_configured():
                logger.info("Skipping account-to-modem refresh because dashboard SQL Server is not configured.")
                return {
                    account_number: self._account_mac_cache[account_number]
                    for account_number in account_numbers
                    if account_number in self._account_mac_cache
                }

            refresh_accounts = missing_accounts if cache_is_fresh else account_numbers
            refreshed_cache: dict[str, str] = {}
            try:
                refreshed_cache = self.dashboard_sql_client.fetch_account_mac_map(
                    refresh_accounts,
                    query_session=query_session,
                )
            except Exception:  # noqa: BLE001
                # If a large lookup times out, retry in smaller batches and keep partial success.
                logger.exception(
                    "Account-to-MAC bulk refresh failed for %s accounts; retrying in smaller chunks.",
                    len(refresh_accounts),
                )
                retry_started = time.monotonic()
                for index, chunk in enumerate(self._iter_chunks(refresh_accounts, self.retry_chunk_size), start=1):
                    if index > self.retry_max_chunks or (time.monotonic() - retry_started) >= self.retry_time_budget_seconds:
                        logger.warning(
                            "Stopping account-to-MAC chunk retries early. processed_chunks=%s max_chunks=%s elapsed_seconds=%.2f",
                            index - 1,
                            self.retry_max_chunks,
                            time.monotonic() - retry_started,
                        )
                        break
                    try:
                        refreshed_cache.update(
                            self.dashboard_sql_client.fetch_account_mac_map(
                                chunk,
                                query_session=query_session,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Account-to-MAC chunk refresh failed. chunk_size=%s",
                            len(chunk),
                        )
            self._account_mac_cache.update(refreshed_cache)
            self._account_mac_refreshed_at = datetime.utcnow()
            logger.info("Refreshed middleware account-to-modem cache. row_count=%s", len(refreshed_cache))
            return {
                account_number: self._account_mac_cache[account_number]
                for account_number in account_numbers
                if account_number in self._account_mac_cache
            }

    def _get_modem_health_rows(
        self,
        mac_addresses: list[str],
        query_session: DashboardSqlQuerySession | None = None,
    ) -> dict[str, dict[str, Any]]:
        stale_macs = []
        now = datetime.utcnow()
        for mac_address in mac_addresses:
            refreshed_at = self._modem_health_refreshed_at.get(mac_address)
            if refreshed_at is None or now - refreshed_at >= self.refresh_interval:
                stale_macs.append(mac_address)

        if stale_macs:
            fetched_rows: dict[str, dict[str, Any]] = {}
            try:
                fetched_rows = self.dashboard_sql_client.fetch_latest_modem_health(
                    stale_macs,
                    query_session=query_session,
                )
            except Exception:  # noqa: BLE001
                # If a large modem batch times out, retry in smaller chunks and keep partial success.
                logger.exception(
                    "Modem-health bulk refresh failed for %s MACs; retrying in smaller chunks.",
                    len(stale_macs),
                )
                retry_started = time.monotonic()
                for index, chunk in enumerate(self._iter_chunks(stale_macs, self.retry_chunk_size), start=1):
                    if index > self.retry_max_chunks or (time.monotonic() - retry_started) >= self.retry_time_budget_seconds:
                        logger.warning(
                            "Stopping modem-health chunk retries early. processed_chunks=%s max_chunks=%s elapsed_seconds=%.2f",
                            index - 1,
                            self.retry_max_chunks,
                            time.monotonic() - retry_started,
                        )
                        break
                    try:
                        fetched_rows.update(
                            self.dashboard_sql_client.fetch_latest_modem_health(
                                chunk,
                                query_session=query_session,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Modem-health chunk refresh failed. chunk_size=%s",
                            len(chunk),
                        )
            with self._lock:
                for mac_address, row in fetched_rows.items():
                    self._modem_health_cache[mac_address] = row
                    self._modem_health_refreshed_at[mac_address] = datetime.utcnow()
                for mac_address in stale_macs:
                    self._modem_health_refreshed_at.setdefault(mac_address, datetime.utcnow())

        return {
            mac_address: self._modem_health_cache[mac_address]
            for mac_address in mac_addresses
            if mac_address in self._modem_health_cache
        }

    def _is_stale(self, refreshed_at: datetime | None) -> bool:
        if refreshed_at is None:
            return True
        return datetime.utcnow() - refreshed_at >= self.refresh_interval

    @staticmethod
    def _normalize_key(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _normalize_mac_key(value: Any) -> str:
        return SqlServerClient.normalize_mac_key(value)