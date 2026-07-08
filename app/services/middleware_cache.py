from datetime import datetime, timedelta
import logging
from threading import RLock
from typing import Any

from app.services.dashboard_sql_client import DashboardSqlClient, DashboardSqlQuerySession
from app.services.sql_server_client import SqlServerClient


logger = logging.getLogger(__name__)


class MiddlewareDataCache:
    def __init__(self, config: dict[str, Any]):
        self.refresh_interval = timedelta(seconds=int(config.get("MODEM_HEALTH_REFRESH_SECONDS", 3600)))
        self.dashboard_sql_client = DashboardSqlClient(config)
        self._lock = RLock()
        self._account_mac_cache: dict[str, str] = {}
        self._account_mac_refreshed_at: datetime | None = None
        self._modem_health_cache: dict[str, dict[str, Any]] = {}
        self._modem_health_refreshed_at: dict[str, datetime] = {}

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
            refreshed_cache = self.dashboard_sql_client.fetch_account_mac_map(
                refresh_accounts,
                query_session=query_session,
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
            fetched_rows = self.dashboard_sql_client.fetch_latest_modem_health(
                stale_macs,
                query_session=query_session,
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