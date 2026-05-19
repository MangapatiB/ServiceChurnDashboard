import logging
import time
from pathlib import Path
from typing import Any

import requests


logger = logging.getLogger(__name__)


class DatabricksClient:
    def __init__(self, config: dict[str, Any]):
        self.host = config.get("DATABRICKS_HOST", "").rstrip("/")
        self.warehouse_id = config.get("DATABRICKS_WAREHOUSE_ID", "")
        self.token = config.get("DATABRICKS_TOKEN", "")
        self.query = config.get("DATABRICKS_SQL_QUERY", "") or self._load_query_from_file(
            config.get("DATABRICKS_SQL_QUERY_FILE", "")
        )

    def is_configured(self) -> bool:
        return all([self.host, self.warehouse_id, self.token, self.query])

    def run_query(self, query: str | None = None) -> list[dict[str, Any]]:
        statement = query or self.query

        if not all([self.host, self.warehouse_id, self.token, statement]):
            logger.error("Databricks connection is not fully configured.")
            raise RuntimeError("Databricks connection is not fully configured.")
        logger.info("Submitting Databricks SQL statement. warehouse_id=%s", self.warehouse_id)
        try:
            response = requests.post(
                f"{self.host}/api/2.0/sql/statements",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "statement": statement,
                    "warehouse_id": self.warehouse_id,
                    "wait_timeout": "30s",
                    "disposition": "INLINE",
                    "format": "JSON_ARRAY",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()

            statement_id = payload.get("statement_id")
            state = payload.get("status", {}).get("state")

            while state in {"PENDING", "RUNNING"}:
                time.sleep(1)
                poll_response = requests.get(
                    f"{self.host}/api/2.0/sql/statements/{statement_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=30,
                )
                poll_response.raise_for_status()
                payload = poll_response.json()
                state = payload.get("status", {}).get("state")

            if state != "SUCCEEDED":
                message = payload.get("status", {}).get("error", {}).get("message", "Unknown Databricks error")
                logger.error("Databricks SQL statement failed. statement_id=%s state=%s message=%s", statement_id, state, message)
                raise RuntimeError(message)

            rows = payload.get("result", {}).get("data_array", [])
            logger.info("Databricks SQL statement succeeded. statement_id=%s row_count=%s", statement_id, len(rows))
            return rows
        except Exception:
            logger.exception("Databricks query execution failed.")
            raise

    @staticmethod
    def _load_query_from_file(file_path: str) -> str:
        if not file_path:
            return ""

        path = Path(file_path)
        if not path.exists():
            return ""

        return path.read_text(encoding="utf-8").strip()
