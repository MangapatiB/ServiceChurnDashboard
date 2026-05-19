from datetime import datetime, timedelta
import logging
from threading import RLock
from typing import Any

from app.services.databricks_client import DatabricksClient
from app.services.query_builders import build_account_mac_mapping_query
from app.services.sql_server_client import SqlServerClient


logger = logging.getLogger(__name__)


class MiddlewareDataCache:
    def __init__(self, config: dict[str, Any]):
        self.refresh_interval = timedelta(seconds=int(config.get("MODEM_HEALTH_REFRESH_SECONDS", 3600)))
        self.databricks_client = DatabricksClient(config)
        self.sql_server_client = SqlServerClient(config)
        self._lock = RLock()
        self._account_mac_cache: dict[str, str] = {}
        self._account_mac_refreshed_at: datetime | None = None
        self._modem_health_cache: dict[str, dict[str, Any]] = {}
        self._modem_health_refreshed_at: dict[str, datetime] = {}

    def get_modem_health_by_account(self, account_numbers: list[str]) -> dict[str, dict[str, Any]]:
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

        account_mac_map = self._get_account_mac_map()
        account_to_mac: dict[str, str] = {}
        for account_number in sanitized_accounts:
            modem_mac = self._normalize_mac_key(account_mac_map.get(account_number))
            if modem_mac:
                account_to_mac[account_number] = modem_mac

        if not account_to_mac:
            return {}

        modem_health_by_mac = self._get_modem_health_rows(list(account_to_mac.values()))
        return {
            account_number: modem_health_by_mac[modem_mac]
            for account_number, modem_mac in account_to_mac.items()
            if modem_mac in modem_health_by_mac
        }

    def _get_account_mac_map(self) -> dict[str, str]:
        with self._lock:
            if self._account_mac_cache and not self._is_stale(self._account_mac_refreshed_at):
                return dict(self._account_mac_cache)

            if not self.databricks_client.is_configured():
                logger.info("Skipping account-to-modem refresh because Databricks is not configured.")
                return dict(self._account_mac_cache)

            rows = self.databricks_client.run_query(build_account_mac_mapping_query())
            refreshed_cache: dict[str, str] = {}
            for row in rows:
                if len(row) < 2:
                    continue
                account_number = self._normalize_key(row[0])
                modem_mac = self._normalize_mac_key(row[1])
                if account_number and modem_mac:
                    refreshed_cache[account_number] = modem_mac

            self._account_mac_cache = refreshed_cache
            self._account_mac_refreshed_at = datetime.utcnow()
            logger.info("Refreshed middleware account-to-modem cache. row_count=%s", len(refreshed_cache))
            return dict(self._account_mac_cache)

    def _get_modem_health_rows(self, mac_addresses: list[str]) -> dict[str, dict[str, Any]]:
        stale_macs = []
        now = datetime.utcnow()
        for mac_address in mac_addresses:
            refreshed_at = self._modem_health_refreshed_at.get(mac_address)
            if refreshed_at is None or now - refreshed_at >= self.refresh_interval:
                stale_macs.append(mac_address)

        if stale_macs:
            fetched_rows = self.sql_server_client.fetch_latest_modem_health(stale_macs)
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