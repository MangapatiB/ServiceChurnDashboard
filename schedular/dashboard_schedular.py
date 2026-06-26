import os
import time
import logging
import json
import random
import threading
import uuid
import hashlib
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pyodbc
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("service-churn-refresh")


def parse_env_table_set(name):
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    return {
        table_name.strip()
        for table_name in raw_value.split(",")
        if table_name.strip()
    }


def build_state_file_path(base_name):
    state_scope = os.getenv("STATE_SCOPE", "").strip()
    if not state_scope:
        return os.path.join(os.path.dirname(__file__), base_name)
    safe_scope = "".join(
        character if character.isalnum() or character in ("-", "_") else "_"
        for character in state_scope
    )
    stem, extension = os.path.splitext(base_name)
    return os.path.join(os.path.dirname(__file__), f"{stem}_{safe_scope}{extension}")


SQL_INSERT_CHUNK_SIZE = 500
MODEM_SOURCE_FETCH_BATCH_SIZE = 5000
MODEM_INSERT_CHUNK_SIZE = int(os.getenv("MODEM_INSERT_CHUNK_SIZE", "1000"))
MODEM_INSERT_COMMIT_EVERY_CHUNKS = int(os.getenv("MODEM_INSERT_COMMIT_EVERY_CHUNKS", "10"))
MODEM_PROGRESS_LOG_EVERY = int(os.getenv("MODEM_PROGRESS_LOG_EVERY", "5000"))
MODEM_STAGE_WORKERS = int(os.getenv("MODEM_STAGE_WORKERS", "4"))
DBX_HTTP_MAX_ATTEMPTS = 4
DBX_HTTP_RETRY_DELAY_SECONDS = 3
DBX_HTTP_RETRY_MAX_DELAY_SECONDS = 30
DBX_HTTP_MAX_CONCURRENT_REQUESTS = int(os.getenv("DBX_HTTP_MAX_CONCURRENT_REQUESTS", "6"))
RES_CHURN_INSERT_CHUNK_SIZE = int(os.getenv("RES_CHURN_INSERT_CHUNK_SIZE", "1000"))
COM_CHURN_INSERT_CHUNK_SIZE = int(os.getenv("COM_CHURN_INSERT_CHUNK_SIZE", "1000"))
CHURN_FETCH_BATCH_SIZE = 5000
CHURN_STAGE_INSERT_COMMIT_EVERY_CHUNKS = 20
CALL_TABLE_INSERT_CHUNK_SIZE = int(os.getenv("CALL_TABLE_INSERT_CHUNK_SIZE", "1000"))
STAGE_INSERT_COMMIT_EVERY_CHUNKS = 10
CALL_TABLE_PROGRESS_LOG_EVERY = 5000
CHURN_PROGRESS_LOG_EVERY = 5000
RESUME_PARTIAL_RUNS = os.getenv("RESUME_PARTIAL_RUNS", "false").strip().lower() == "true"
RUN_ONLY_TABLES = parse_env_table_set("RUN_ONLY_TABLES")
SKIP_TABLES = parse_env_table_set("SKIP_TABLES") or set()
CHURN_FETCH_WORKERS = 6
CHURN_STAGE_WORKERS = int(os.getenv("CHURN_STAGE_WORKERS", "8"))
CALL_TABLE_STAGE_WORKERS = int(os.getenv("CALL_TABLE_STAGE_WORKERS", "8"))
TOP_LEVEL_DATA_FETCH_WORKERS = int(os.getenv("TOP_LEVEL_DATA_FETCH_WORKERS", "8"))
CALL_MONTHLY_FETCH_WORKERS = 4
CALL_RECORDS_FETCH_BATCH_SIZE = 5000
CALL_RECORDS_FETCH_WORKERS = 4
CHECKPOINT_FILE = build_state_file_path(".dashboard_schedular_checkpoint.json")
SOURCE_STATE_FILE = build_state_file_path(".dashboard_schedular_source_state.json")
SOURCE_AND_TARGET_SHARE_SERVER = None
DBX_REQUEST_SEMAPHORE = threading.BoundedSemaphore(DBX_HTTP_MAX_CONCURRENT_REQUESTS)


TRUCKROLL_QUERY = """
WITH TR AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY accountnumber ORDER BY LoadTimestampId DESC) AS rank
    FROM prod.bronze.truckrollautomation_truckrollpredictionsbatch
),
SR AS (
    SELECT DISTINCT LegacyAccountId, SubscriberAccountNumber, BillingCity
    FROM prod.silver.dnadatawarehouse_subscriberhistory
),
HSD AS (
    SELECT SubscriberAccountNumber,
           MAX(CASE
                   WHEN regexp_like(ProductTypeCode, '^I[0-9]+$')
                        OR ProductTypeCode IN ('IA', 'IC', 'IE', 'IF', 'II', 'IU')
                   THEN 1 ELSE 0
               END) AS HSD
    FROM prod.silver.billingsystem_product_history
    GROUP BY SubscriberAccountNumber
),
TRSR AS (
    SELECT *
    FROM TR
    LEFT JOIN SR ON TR.accountnumber = SR.LegacyAccountId
    WHERE TR.rank = 1
)
SELECT DISTINCT
    CAST(TRSR.accountnumber AS STRING) AS LegacyAccountNumber,
    CAST(TRSR.SubscriberAccountNumber AS STRING) AS SubscriberAccountNumber,
    CAST(PULSE.PhoneNumber AS STRING) AS PhoneNumber,
    UPPER(TRSR.BillingCity) AS BillingCity
FROM TRSR
LEFT JOIN HSD ON HSD.SubscriberAccountNumber = TRSR.SubscriberAccountNumber
INNER JOIN prod.bronze.pulsedb_optin AS PULSE
    ON PULSE.AccountNumber = TRSR.accountnumber
WHERE TRSR.prediction = 1
  AND LOWER(PULSE.OptOutStatus) = 'false'
  AND HSD.HSD = 1
  AND TRSR.SubscriberAccountNumber IS NOT NULL
"""

RES_CHURN_QUERY = """
WITH latest_prediction_month AS (
    SELECT MAX(prediction_month) AS PredictionMonth
    FROM prod.featurestore.res_shap_category_v2
),
churn_source AS (
    SELECT
        FORMAT_STRING('%.0f', SubscriberAccountNumber) AS SubscriberAccountNumber,
        churn_probability * 100 AS ChurnProbability,
        prediction_month AS PredictionMonth,
        CASE top1_feature
            WHEN 'high_charges' THEN 'High Charges (Pricing & Bill Changes)'
            WHEN 'product_limits' THEN 'Product & Plan (Product Limits)'
            WHEN 'high_support_interaction' THEN 'Support Interaction (Calls & Contacts)'
            WHEN 'past_churn_behavior' THEN 'Past Churn Behavior'
            WHEN 'promotion_ending' THEN 'Promotion & Discounts'
            WHEN 'truckroll_AND_Outtage' THEN 'Past Truckrolls'
            WHEN 'financial_distress' THEN 'Financial Distress'
            WHEN 'competitive_pressure' THEN 'Competitive Pressure (Competition)'
            ELSE top1_feature
        END AS Top1Feature,
        CASE top2_feature
            WHEN 'high_charges' THEN 'High Charges (Pricing & Bill Changes)'
            WHEN 'product_limits' THEN 'Product & Plan (Product Limits)'
            WHEN 'high_support_interaction' THEN 'Support Interaction (Calls & Contacts)'
            WHEN 'past_churn_behavior' THEN 'Past Churn Behavior'
            WHEN 'promotion_ending' THEN 'Promotion & Discounts'
            WHEN 'truckroll_AND_Outtage' THEN 'Past Truckrolls'
            WHEN 'financial_distress' THEN 'Financial Distress'
            WHEN 'competitive_pressure' THEN 'Competitive Pressure (Competition)'
            ELSE top2_feature
        END AS Top2Feature,
        CASE top3_feature
            WHEN 'high_charges' THEN 'High Charges (Pricing & Bill Changes)'
            WHEN 'product_limits' THEN 'Product & Plan (Product Limits)'
            WHEN 'high_support_interaction' THEN 'Support Interaction (Calls & Contacts)'
            WHEN 'past_churn_behavior' THEN 'Past Churn Behavior'
            WHEN 'promotion_ending' THEN 'Promotion & Discounts'
            WHEN 'truckroll_AND_Outtage' THEN 'Past Truckrolls'
            WHEN 'financial_distress' THEN 'Financial Distress'
            WHEN 'competitive_pressure' THEN 'Competitive Pressure (Competition)'
            ELSE top3_feature
        END AS Top3Feature
    FROM prod.featurestore.res_shap_category_v2
)
SELECT *
FROM churn_source
WHERE PredictionMonth = (
    SELECT PredictionMonth
    FROM latest_prediction_month
)
"""

COM_CHURN_QUERY = RES_CHURN_QUERY.replace(
    "prod.featurestore.res_shap_category_v2",
    "prod.featurestore.com_shap_category_v2",
)

CHURN_BASE_PREFIX_LENGTH = 8
CHURN_SPLIT_PREFIX_LENGTH = 9
CHURN_MAX_ROWS_PER_BATCH = 50000
CALL_MONTH_BATCHES = 13
RES_CHURN_PREPARED_SOURCE = os.getenv("RES_CHURN_PREPARED_SOURCE")
COM_CHURN_PREPARED_SOURCE = os.getenv("COM_CHURN_PREPARED_SOURCE")
CALL_MONTHLY_AGG_PREPARED_SOURCE = os.getenv("CALL_MONTHLY_AGG_PREPARED_SOURCE")
CALL_RECORDS_MONTHLY_PREPARED_SOURCE = os.getenv("CALL_RECORDS_MONTHLY_PREPARED_SOURCE")

ACCOUNT_MAC_QUERY = """
SELECT
    CAST(accountnumber AS STRING) AS AccountNumber,
    UPPER(REPLACE(TRIM(cmac), ':', '')) AS ModemMac
FROM prod.featurestore.cmdata_15day
WHERE accountnumber IS NOT NULL
  AND cmac IS NOT NULL
"""

CALL_MONTHLY_AGG_QUERY = """
SELECT
    CAST(ctn.SUB_ACCT_NO_CTN AS STRING) AS AccountNumber,
    UPPER(TRIM(sbb.CUST_TYP_SBB)) AS CustomerType,
    DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS MonthStart,
    DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS ContactMonthStart,
    COUNT(*) AS NumberOfCalls,
    ROUND(AVG((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS AverageAgentTalkMin,
    ROUND(AVG((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS AverageTotalContactDurationMin,
    ROUND(SUM((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS TotalAgentTalkMin,
    ROUND(SUM((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS TotalContactDurationMin
FROM prod.bronze.dnadatawarehouse_ctn_interaction ctn
INNER JOIN prod.bronze.dnadatawarehouse_sbb_base sbb
    ON ctn.CUST_ACCT_NO_CTN = sbb.CUST_ACCT_NO_SBB
WHERE ctn.INTR_TYP_CTN IN ('Call In', 'Outbound Call')
  AND ctn.START_DTE_CTN >= DATE_ADD(CURRENT_DATE(), -365)
  AND ctn.SUB_ACCT_NO_CTN IS NOT NULL
GROUP BY
    CAST(ctn.SUB_ACCT_NO_CTN AS STRING),
    UPPER(TRIM(sbb.CUST_TYP_SBB)),
    DATE_TRUNC('month', ctn.START_DTE_TME_CTN)
"""

CALL_RECORDS_MONTHLY_QUERY = """
WITH latest_transcriptions AS (
    SELECT
        ct.contact_id,
        ct.from_address,
        ct.client_phone,
        ct.start_time,
        ct.is_resolved,
        ct.client_sentiment,
        ROW_NUMBER() OVER (PARTITION BY ct.contact_id ORDER BY ct.start_time DESC) AS row_rank
    FROM prod.bronze.call_transcriptions ct
),
subscriber_phone_map AS (
    SELECT DISTINCT
        CAST(s.SubscriberAccountNumber AS STRING) AS SubscriberAccount,
        CAST(s.CustomerAccountNumber AS STRING) AS CustomerAccount,
        REGEXP_REPLACE(TRIM(CAST(s.CustomerPhoneNumber AS STRING)), '^\\+1', '') AS NormalizedCustomerPhone
    FROM prod.silver.billingsystem_subscriber_history s
    WHERE s.SubscriberAccountNumber IS NOT NULL
      AND s.CustomerPhoneNumber IS NOT NULL
),
monthly_contact_sentiment AS (
    SELECT
        spm.CustomerAccount,
        spm.SubscriberAccount,
        DATE_TRUNC('month', lt.start_time) AS MonthStart,
        MAX_BY(COALESCE(lt.client_sentiment, 'UNKNOWN'), lt.start_time) AS LatestClientSentiment,
        MAX_BY(CAST(COALESCE(lt.is_resolved, FALSE) AS STRING), lt.start_time) AS LatestIsResolved
    FROM latest_transcriptions lt
    INNER JOIN subscriber_phone_map spm
        ON REGEXP_REPLACE(TRIM(COALESCE(lt.from_address, lt.client_phone)), '^\\+1', '') = spm.NormalizedCustomerPhone
    WHERE lt.row_rank = 1
      AND lt.start_time IS NOT NULL
    GROUP BY
        spm.CustomerAccount,
        spm.SubscriberAccount,
        DATE_TRUNC('month', lt.start_time)
),
ctn_summary AS (
    SELECT
        CAST(ctn.CUST_ACCT_NO_CTN AS STRING) AS CustomerAccount,
        CAST(ctn.SUB_ACCT_NO_CTN AS STRING) AS SubscriberAccount,
        UPPER(TRIM(sbb.CUST_TYP_SBB)) AS CustomerType,
        DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS MonthStart,
        COUNT(*) AS NumberOfCalls,
        ROUND(SUM((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS TotalDurationMinutes,
        ROUND(AVG((unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0), 2) AS AvgDurationMinutes
    FROM prod.bronze.dnadatawarehouse_ctn_interaction ctn
    INNER JOIN prod.bronze.dnadatawarehouse_sbb_base sbb
        ON ctn.CUST_ACCT_NO_CTN = sbb.CUST_ACCT_NO_SBB
    WHERE ctn.INTR_TYP_CTN IN ('Call In', 'Outbound Call')
      AND ctn.START_DTE_CTN >= DATE_ADD(CURRENT_DATE(), -365)
      AND ctn.SUB_ACCT_NO_CTN IS NOT NULL
    GROUP BY
        CAST(ctn.CUST_ACCT_NO_CTN AS STRING),
        CAST(ctn.SUB_ACCT_NO_CTN AS STRING),
        UPPER(TRIM(sbb.CUST_TYP_SBB)),
        DATE_TRUNC('month', ctn.START_DTE_TME_CTN)
)
SELECT
    ctn.CustomerAccount,
    ctn.SubscriberAccount,
    ctn.CustomerType,
    ctn.MonthStart,
    ctn.NumberOfCalls,
    ctn.TotalDurationMinutes,
    ctn.AvgDurationMinutes,
    COALESCE(mcs.LatestClientSentiment, 'UNKNOWN') AS LatestClientSentiment,
    COALESCE(mcs.LatestIsResolved, 'false') AS LatestIsResolved
FROM ctn_summary ctn
LEFT JOIN monthly_contact_sentiment mcs
    ON ctn.CustomerAccount = mcs.CustomerAccount
   AND ctn.SubscriberAccount = mcs.SubscriberAccount
   AND ctn.MonthStart = mcs.MonthStart
"""

SOURCE_MODEM_QUERY = """
WITH ranked_modems AS (
    SELECT
        UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) AS ModemMac,
        ip AS IP,
        tstamp AS LastSeen,
        usint AS USINT,
        CASE
            WHEN LOWER(LTRIM(RTRIM(CONVERT(VARCHAR(255), state)))) IN ('online', 'offline')
                THEN UPPER(LEFT(CONVERT(VARCHAR(255), state), 1)) + LOWER(SUBSTRING(CONVERT(VARCHAR(255), state), 2, 255))
            WHEN ip IS NULL OR ip = '0.0.0.0' THEN 'Offline'
            ELSE 'Online'
        END AS Status,
        state AS State,
        usrxlvl AS USRXLVL,
        ustxpwr AS USTXPWR,
        usrxsnr AS USRXSNR,
        dsrxlvl AS DSRXLVL,
        dsrxsnr AS DSRXSNR,
        dsprefec AS DSPREFEC,
        dspostfec AS DSPOSTFEC,
        dsbw AS DSBW,
        usbw AS USBW,
        fibernode AS FiberNode,
        cmts AS CMTS,
        ROW_NUMBER() OVER (
            PARTITION BY UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', ''))
            ORDER BY tstamp DESC
        ) AS row_rank
    FROM newbacondata.dbo.cmdata2011
    WHERE ip <> '0.0.0.0'
)
SELECT
    ModemMac, IP, LastSeen, USINT, Status, State, USRXLVL, USTXPWR, USRXSNR,
    DSRXLVL, DSRXSNR, DSPREFEC, DSPOSTFEC, DSBW, USBW, FiberNode, CMTS
FROM ranked_modems
WHERE row_rank = 1
"""


def databricks_query(statement):
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    token = os.environ["DATABRICKS_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}

    def retry_delay_seconds(attempt):
        backoff_delay = DBX_HTTP_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
        capped_delay = min(backoff_delay, DBX_HTTP_RETRY_MAX_DELAY_SECONDS)
        return capped_delay + random.uniform(0, 1)

    def request_json(method, url, **kwargs):
        last_error = None
        for attempt in range(1, DBX_HTTP_MAX_ATTEMPTS + 1):
            try:
                response = requests.request(method, url, headers=headers, timeout=60, **kwargs)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt == DBX_HTTP_MAX_ATTEMPTS:
                    raise
                retry_delay = retry_delay_seconds(attempt)
                logger.warning(
                    "Databricks HTTP request failed on attempt %s/%s: %s; retrying in %.2fs",
                    attempt,
                    DBX_HTTP_MAX_ATTEMPTS,
                    exc,
                    retry_delay,
                )
                time.sleep(retry_delay)

        raise last_error

    with DBX_REQUEST_SEMAPHORE:
        payload = request_json(
            "post",
            f"{host}/api/2.0/sql/statements",
            json={
                "statement": statement,
                "warehouse_id": warehouse_id,
                "wait_timeout": "30s",
                "disposition": "INLINE",
                "format": "JSON_ARRAY",
            },
        )

        statement_id = payload.get("statement_id")
        state = payload.get("status", {}).get("state")
        while state in {"PENDING", "RUNNING"}:
            time.sleep(1)
            payload = request_json(
                "get",
                f"{host}/api/2.0/sql/statements/{statement_id}",
            )
            state = payload.get("status", {}).get("state")

    if state != "SUCCEEDED":
        raise RuntimeError(payload.get("status", {}).get("error", {}).get("message", "Databricks query failed"))

    return payload.get("result", {}).get("data_array", [])


def build_churn_batch_query(base_query, account_prefix):
    return (
        "SELECT *\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") churn_batch\n"
        f"WHERE SubscriberAccountNumber LIKE '{account_prefix}%'"
    )


def build_churn_prefix_count_query(base_query, prefix_length, parent_prefix=None):
    where_clause = ""
    if parent_prefix:
        where_clause = f"WHERE SubscriberAccountNumber LIKE '{parent_prefix}%'\n"
    return (
        "SELECT SUBSTRING(SubscriberAccountNumber, 1, "
        f"{prefix_length}"
        ") AS BatchPrefix, COUNT(*) AS RowCount\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") churn_prefixes\n"
        f"{where_clause}"
        "GROUP BY SUBSTRING(SubscriberAccountNumber, 1, "
        f"{prefix_length}"
        ")\n"
        "ORDER BY BatchPrefix"
    )


def get_churn_batch_details(base_query):
    adaptive_prefixes = []
    prefix_rows = databricks_query(
        build_churn_prefix_count_query(base_query, CHURN_BASE_PREFIX_LENGTH)
    )
    for prefix_row in prefix_rows:
        if not prefix_row or not prefix_row[0]:
            continue
        prefix = prefix_row[0]
        row_count = int(prefix_row[1]) if prefix_row[1] not in (None, "") else 0
        if row_count > CHURN_MAX_ROWS_PER_BATCH:
            child_prefix_rows = databricks_query(
                build_churn_prefix_count_query(
                    base_query,
                    CHURN_SPLIT_PREFIX_LENGTH,
                    parent_prefix=prefix,
                )
            )
            adaptive_prefixes.extend([
                (
                    row[0],
                    int(row[1]) if len(row) > 1 and row[1] not in (None, "") else 0,
                )
                for row in child_prefix_rows
                if row and row[0]
            ])
        else:
            adaptive_prefixes.append((prefix, row_count))
    return adaptive_prefixes


def get_churn_batch_prefixes(base_query):
    return [prefix for prefix, _ in get_churn_batch_details(base_query)]


def fetch_parallel_churn_batches(base_query, account_prefixes, max_workers, query_label):
    if not account_prefixes:
        logger.info("No churn prefixes found for %s", query_label)
        return []

    logger.info(
        "Fetching %s churn prefixes for %s with workers=%s",
        len(account_prefixes),
        query_label,
        max_workers,
    )

    rows_by_prefix = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_prefix = {
            executor.submit(
                databricks_query,
                build_churn_batch_query(base_query, account_prefix),
            ): account_prefix
            for account_prefix in account_prefixes
        }
        for future in as_completed(future_to_prefix):
            account_prefix = future_to_prefix[future]
            batch_rows = future.result()
            rows_by_prefix[account_prefix] = batch_rows
            logger.info(
                "%s prefix=%s fetched=%s",
                query_label,
                account_prefix,
                len(batch_rows),
            )

    ordered_rows = []
    for account_prefix in account_prefixes:
        ordered_rows.extend(rows_by_prefix.get(account_prefix, []))
    return ordered_rows


def partition_list_round_robin(values, partition_count):
    partitions = [[] for _ in range(partition_count)]
    for index, value in enumerate(values):
        partitions[index % partition_count].append(value)
    return [partition for partition in partitions if partition]


def partition_weighted_churn_prefixes(prefix_details, partition_count):
    worker_buckets = [
        {"estimated_rows": 0, "prefixes": []}
        for _ in range(partition_count)
    ]

    for prefix, row_count in sorted(prefix_details, key=lambda item: item[1], reverse=True):
        lightest_bucket = min(worker_buckets, key=lambda bucket: bucket["estimated_rows"])
        lightest_bucket["prefixes"].append(prefix)
        lightest_bucket["estimated_rows"] += row_count

    return [
        bucket["prefixes"]
        for bucket in worker_buckets
        if bucket["prefixes"]
    ]


def load_churn_prefix_partition_to_stage(
    target_table,
    insert_columns,
    insert_sql_template,
    base_query,
    account_prefixes,
    query_label,
    worker_index,
):
    worker_connection = sql_connect("TARGET_SQL")
    worker_cursor = worker_connection.cursor()
    stage_table = make_shared_stage_table_name(target_table, worker_index)
    total_rows = 0

    try:
        create_shared_stage_table(worker_cursor, target_table, stage_table)
        insert_sql = insert_sql_template.format(stage_table=stage_table)

        for account_prefix in account_prefixes:
            prefix_rows = normalize_churn_rows(
                databricks_query(build_churn_batch_query(base_query, account_prefix))
            )
            logger.info(
                "%s worker=%s prefix=%s fetched=%s",
                query_label,
                worker_index,
                account_prefix,
                len(prefix_rows),
            )
            append_rows_with_mode(
                worker_cursor,
                insert_sql,
                prefix_rows,
                use_fast_executemany=False,
                chunk_size=RES_CHURN_INSERT_CHUNK_SIZE if query_label == "residential churn" else COM_CHURN_INSERT_CHUNK_SIZE,
                commit_connection=worker_connection,
                commit_every_chunks=CHURN_STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                progress_label=f"{query_label} worker={worker_index}",
                progress_log_every=CHURN_PROGRESS_LOG_EVERY,
                batch_timing_label=f"{query_label} worker={worker_index} insert",
            )
            total_rows += len(prefix_rows)

        return stage_table, total_rows, worker_connection, worker_cursor
    except Exception:
        safe_rollback(worker_connection)
        try:
            drop_stage_table(worker_cursor, stage_table)
            worker_connection.commit()
        except Exception:
            pass
        worker_connection.close()
        raise


def load_parallel_churn_stage_tables(
    target_table,
    insert_columns,
    base_query,
    account_prefixes,
    query_label,
    max_workers,
):
    if not account_prefixes:
        logger.info("No churn prefixes found for %s", query_label)
        return [], 0

    if account_prefixes and isinstance(account_prefixes[0], tuple):
        worker_partitions = partition_weighted_churn_prefixes(
            account_prefixes,
            min(max_workers, len(account_prefixes)),
        )
        logger.info(
            "%s weighted worker row estimates=%s",
            query_label,
            [
                sum(row_count for prefix, row_count in account_prefixes if prefix in partition)
                for partition in worker_partitions
            ],
        )
    else:
        worker_partitions = partition_list_round_robin(
            account_prefixes,
            min(max_workers, len(account_prefixes)),
        )
    insert_sql_template = build_insert_sql("{stage_table}", insert_columns).replace(
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        "VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
    )

    logger.info(
        "Loading %s churn prefixes for %s across workers=%s",
        len(account_prefixes),
        query_label,
        len(worker_partitions),
    )

    stage_table_handles = []
    total_rows = 0
    try:
        with ThreadPoolExecutor(max_workers=len(worker_partitions)) as executor:
            future_to_worker = {
                executor.submit(
                    load_churn_prefix_partition_to_stage,
                    target_table,
                    insert_columns,
                    insert_sql_template,
                    base_query,
                    prefix_partition,
                    query_label,
                    worker_index,
                ): worker_index
                for worker_index, prefix_partition in enumerate(worker_partitions, start=1)
            }
            for future in as_completed(future_to_worker):
                worker_index = future_to_worker[future]
                stage_table, row_count, worker_connection, worker_cursor = future.result()
                stage_table_handles.append((stage_table, worker_connection, worker_cursor))
                total_rows += row_count
                logger.info(
                    "%s worker=%s stage=%s loaded_rows=%s",
                    query_label,
                    worker_index,
                    stage_table,
                    row_count,
                )
    except Exception:
        cleanup_parallel_stage_tables(stage_table_handles)
        raise

    return stage_table_handles, total_rows


def partition_rows_evenly(rows, partition_count):
    if not rows or partition_count <= 0:
        return []
    partition_size = max(1, (len(rows) + partition_count - 1) // partition_count)
    partitions = []
    for start_index in range(0, len(rows), partition_size):
        partitions.append(rows[start_index:start_index + partition_size])
    return partitions


def load_row_partition_to_stage(
    target_table,
    insert_columns,
    insert_sql_template,
    rows,
    query_label,
    chunk_size,
    commit_every_chunks,
    progress_log_every,
    worker_index,
):
    worker_connection = sql_connect("TARGET_SQL")
    worker_cursor = worker_connection.cursor()
    stage_table = make_shared_stage_table_name(target_table, worker_index)

    try:
        create_shared_stage_table(worker_cursor, target_table, stage_table)
        insert_sql = insert_sql_template.format(stage_table=stage_table)
        append_rows_with_mode(
            worker_cursor,
            insert_sql,
            rows,
            use_fast_executemany=False,
            chunk_size=chunk_size,
            commit_connection=worker_connection,
            commit_every_chunks=commit_every_chunks,
            progress_label=f"{query_label} worker={worker_index}",
            progress_log_every=progress_log_every,
            batch_timing_label=f"{query_label} worker={worker_index} insert",
        )
        return stage_table, len(rows), worker_connection, worker_cursor
    except Exception:
        safe_rollback(worker_connection)
        try:
            drop_stage_table(worker_cursor, stage_table)
            worker_connection.commit()
        except Exception:
            pass
        worker_connection.close()
        raise


def load_parallel_rows_to_stage_tables(
    target_table,
    insert_columns,
    rows,
    query_label,
    max_workers,
    chunk_size,
    commit_every_chunks,
    progress_log_every,
):
    if not rows:
        logger.info("No rows found for %s", query_label)
        return [], 0

    worker_partitions = partition_rows_evenly(rows, min(max_workers, len(rows)))
    insert_sql_template = build_insert_sql("{stage_table}", insert_columns).replace(
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
    )

    logger.info(
        "Loading %s rows for %s across workers=%s",
        len(rows),
        query_label,
        len(worker_partitions),
    )

    stage_table_handles = []
    total_rows = 0
    try:
        with ThreadPoolExecutor(max_workers=len(worker_partitions)) as executor:
            future_to_worker = {
                executor.submit(
                    load_row_partition_to_stage,
                    target_table,
                    insert_columns,
                    insert_sql_template,
                    row_partition,
                    query_label,
                    chunk_size,
                    commit_every_chunks,
                    progress_log_every,
                    worker_index,
                ): worker_index
                for worker_index, row_partition in enumerate(worker_partitions, start=1)
            }
            for future in as_completed(future_to_worker):
                worker_index = future_to_worker[future]
                stage_table, row_count, worker_connection, worker_cursor = future.result()
                stage_table_handles.append((stage_table, worker_connection, worker_cursor))
                total_rows += row_count
                logger.info(
                    "%s worker=%s stage=%s loaded_rows=%s",
                    query_label,
                    worker_index,
                    stage_table,
                    row_count,
                )
    except Exception:
        cleanup_parallel_stage_tables(stage_table_handles)
        raise

    return stage_table_handles, total_rows


def cleanup_parallel_stage_tables(stage_table_handles):
    for stage_table, worker_connection, worker_cursor in stage_table_handles:
        try:
            drop_stage_table(worker_cursor, stage_table)
            worker_connection.commit()
        except Exception:
            safe_rollback(worker_connection)
        finally:
            worker_connection.close()


def load_parallel_modem_rows_to_stage_tables(target_table, rows):
    if not rows:
        return [], 0

    insert_columns = [
        "ModemMac", "IP", "LastSeen", "USINT", "Status", "State", "USRXLVL", "USTXPWR", "USRXSNR",
        "DSRXLVL", "DSRXSNR", "DSPREFEC", "DSPOSTFEC", "DSBW", "USBW", "FiberNode", "CMTS", "RefreshedAt",
    ]
    worker_partitions = partition_rows_evenly(rows, min(MODEM_STAGE_WORKERS, len(rows)))
    insert_sql_template = build_insert_sql("{stage_table}", insert_columns).replace(
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
    )

    logger.info(
        "Loading %s modem health rows across workers=%s",
        len(rows),
        len(worker_partitions),
    )

    stage_table_handles = []
    total_rows = 0
    try:
        with ThreadPoolExecutor(max_workers=len(worker_partitions)) as executor:
            future_to_worker = {
                executor.submit(
                    load_row_partition_to_stage,
                    target_table,
                    insert_columns,
                    insert_sql_template,
                    row_partition,
                    "modem health",
                    MODEM_INSERT_CHUNK_SIZE,
                    MODEM_INSERT_COMMIT_EVERY_CHUNKS,
                    MODEM_PROGRESS_LOG_EVERY,
                    worker_index,
                ): worker_index
                for worker_index, row_partition in enumerate(worker_partitions, start=1)
            }
            for future in as_completed(future_to_worker):
                worker_index = future_to_worker[future]
                stage_table, row_count, worker_connection, worker_cursor = future.result()
                stage_table_handles.append((stage_table, worker_connection, worker_cursor))
                total_rows += row_count
                logger.info(
                    "modem health worker=%s stage=%s loaded_rows=%s",
                    worker_index,
                    stage_table,
                    row_count,
                )
    except Exception:
        cleanup_parallel_stage_tables(stage_table_handles)
        raise

    return stage_table_handles, total_rows


def load_churn_offset_partition_to_stage(
    target_table,
    insert_columns,
    insert_sql_template,
    base_query,
    batch_offsets,
    batch_size,
    order_by_clause,
    query_label,
    worker_index,
):
    worker_connection = sql_connect("TARGET_SQL")
    worker_cursor = worker_connection.cursor()
    stage_table = make_shared_stage_table_name(target_table, worker_index)
    total_rows = 0

    try:
        create_shared_stage_table(worker_cursor, target_table, stage_table)
        insert_sql = insert_sql_template.format(stage_table=stage_table)

        for offset in batch_offsets:
            batch_rows = normalize_churn_rows(
                databricks_query(
                    build_limit_offset_query(base_query, order_by_clause, batch_size, offset)
                )
            )
            logger.info(
                "%s worker=%s offset=%s fetched=%s",
                query_label,
                worker_index,
                offset,
                len(batch_rows),
            )
            append_rows_with_mode(
                worker_cursor,
                insert_sql,
                batch_rows,
                use_fast_executemany=False,
                chunk_size=RES_CHURN_INSERT_CHUNK_SIZE if query_label == "residential churn" else COM_CHURN_INSERT_CHUNK_SIZE,
                commit_connection=worker_connection,
                commit_every_chunks=STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                progress_label=f"{query_label} worker={worker_index}",
                progress_log_every=CHURN_PROGRESS_LOG_EVERY,
            )
            total_rows += len(batch_rows)

        return stage_table, total_rows, worker_connection, worker_cursor
    except Exception:
        safe_rollback(worker_connection)
        try:
            drop_stage_table(worker_cursor, stage_table)
            worker_connection.commit()
        except Exception:
            pass
        worker_connection.close()
        raise


def load_parallel_churn_offset_stage_tables(
    target_table,
    insert_columns,
    base_query,
    query_label,
    max_workers,
    batch_size,
    order_by_clause,
):
    total_rows, batch_offsets = get_databricks_batch_offsets(base_query, batch_size)
    if total_rows == 0:
        logger.info("No rows found for %s", query_label)
        return [], 0

    worker_partitions = partition_list_round_robin(
        batch_offsets,
        min(max_workers, len(batch_offsets)),
    )
    insert_sql_template = build_insert_sql("{stage_table}", insert_columns).replace(
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        "VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
    )

    logger.info(
        "Loading %s churn rows for %s in %s offset batches across workers=%s",
        total_rows,
        query_label,
        len(batch_offsets),
        len(worker_partitions),
    )

    stage_table_handles = []
    loaded_rows = 0
    try:
        with ThreadPoolExecutor(max_workers=len(worker_partitions)) as executor:
            future_to_worker = {
                executor.submit(
                    load_churn_offset_partition_to_stage,
                    target_table,
                    insert_columns,
                    insert_sql_template,
                    base_query,
                    offset_partition,
                    batch_size,
                    order_by_clause,
                    query_label,
                    worker_index,
                ): worker_index
                for worker_index, offset_partition in enumerate(worker_partitions, start=1)
            }
            for future in as_completed(future_to_worker):
                worker_index = future_to_worker[future]
                stage_table, row_count, worker_connection, worker_cursor = future.result()
                stage_table_handles.append((stage_table, worker_connection, worker_cursor))
                loaded_rows += row_count
                logger.info(
                    "%s worker=%s stage=%s loaded_rows=%s",
                    query_label,
                    worker_index,
                    stage_table,
                    row_count,
                )
    except Exception:
        cleanup_parallel_stage_tables(stage_table_handles)
        raise

    return stage_table_handles, loaded_rows


def fetch_residential_churn_rows():
    res_source_query = get_res_churn_source_query()
    return fetch_churn_rows(
        res_source_query,
        "residential churn",
    )


def fetch_truckroll_rows():
    return databricks_query(TRUCKROLL_QUERY)


def fetch_account_mac_rows():
    return databricks_query(ACCOUNT_MAC_QUERY)


def fetch_modem_rows():
    source_sql = sql_connect("SOURCE_SQL")
    try:
        source_cursor = source_sql.cursor()
        source_cursor.execute(SOURCE_MODEM_QUERY)
        normalized_rows = []
        while True:
            modem_batch = source_cursor.fetchmany(MODEM_SOURCE_FETCH_BATCH_SIZE)
            if not modem_batch:
                break
            normalized_modem_batch = normalize_modem_rows([tuple(row) for row in modem_batch])
            normalized_rows.extend(normalized_modem_batch)
            logger.info("Fetched modem health batch size=%s", len(normalized_modem_batch))
        return normalized_rows
    finally:
        source_sql.close()


def fetch_commercial_churn_rows():
    com_source_query = get_com_churn_source_query()
    return fetch_churn_rows(
        com_source_query,
        "commercial churn",
    )


def fetch_churn_rows(base_query, query_label):
    prepared_source_enabled = query_label == "residential churn" and RES_CHURN_PREPARED_SOURCE
    prepared_source_enabled = prepared_source_enabled or (query_label == "commercial churn" and COM_CHURN_PREPARED_SOURCE)
    if prepared_source_enabled:
        logger.info("Loading %s from prepared source", query_label)
        return normalize_churn_rows(databricks_query(base_query))

    batch_details_started = time.perf_counter()
    churn_batch_details = get_churn_batch_details(base_query)
    log_phase_duration(f"{query_label} batch discovery", batch_details_started, len(churn_batch_details))
    logger.info("%s adaptive batch count=%s", query_label, len(churn_batch_details))

    fetch_started = time.perf_counter()
    churn_rows = fetch_parallel_churn_batches(
        base_query,
        [prefix for prefix, _ in churn_batch_details],
        CHURN_FETCH_WORKERS,
        query_label,
    )
    log_phase_duration(f"{query_label} prefix fetch", fetch_started, len(churn_rows))

    return normalize_churn_rows(
        churn_rows
    )


def fetch_parallel_monthly_batches(base_query, month_batches, max_workers, query_label):
    if not month_batches:
        logger.info("No monthly batches found for %s", query_label)
        return []

    logger.info(
        "Fetching %s month batches for %s with workers=%s",
        len(month_batches),
        query_label,
        max_workers,
    )

    rows_by_month = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_month = {
            executor.submit(
                databricks_query,
                build_month_batch_query(base_query, month_start),
            ): month_start
            for month_start in month_batches
        }
        for future in as_completed(future_to_month):
            month_start = future_to_month[future]
            month_label = month_start.strftime("%Y-%m")
            batch_rows = future.result()
            rows_by_month[month_start] = batch_rows
            logger.info(
                "%s month=%s fetched=%s",
                query_label,
                month_label,
                len(batch_rows),
            )

    ordered_rows = []
    for month_start in month_batches:
        ordered_rows.extend(rows_by_month.get(month_start, []))
    return ordered_rows


def fetch_call_monthly_agg_rows(month_batches):
    call_monthly_source_query = get_call_monthly_agg_source_query()
    return normalize_call_monthly_rows(
        fetch_parallel_monthly_batches(
            call_monthly_source_query,
            month_batches,
            CALL_MONTHLY_FETCH_WORKERS,
            "call monthly aggregate",
        )
    )


def fetch_call_records_monthly_rows(month_batches):
    call_records_source_query = get_call_records_source_query()
    normalized_rows = []
    for month_start in month_batches:
        month_label = month_start.strftime("%Y-%m")
        logger.info("Loading call records batch month=%s", month_label)
        call_records_rows = fetch_parallel_databricks_batches(
            build_month_batch_query(call_records_source_query, month_start),
            "MonthStart, CustomerAccount, SubscriberAccount, CustomerType",
            CALL_RECORDS_FETCH_BATCH_SIZE,
            CALL_RECORDS_FETCH_WORKERS,
            f"call records month={month_label}",
        )
        normalized_rows.extend(normalize_call_records_rows(call_records_rows))
    return normalized_rows


def build_month_batch_query(base_query, month_start):
    month_literal = month_start.strftime("%Y-%m-01")
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    next_month_literal = next_month.strftime("%Y-%m-01")
    return (
        "SELECT *\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") batched_results\n"
        f"WHERE MonthStart >= DATE '{month_literal}'\n"
        f"  AND MonthStart < DATE '{next_month_literal}'"
    )


def build_count_query(base_query):
    return (
        "SELECT COUNT(*) AS RowCount\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") counted_results"
    )


def build_source_fingerprint_query(base_query, columns):
    hash_inputs = ",\n        ".join(
        [f"COALESCE(CAST({column} AS STRING), '<NULL>')" for column in columns]
    )
    return (
        "SELECT\n"
        "    COUNT(*) AS RowCount,\n"
        "    COALESCE(CAST(SUM(CAST(conv(substr(md5(concat_ws(''||'',\n"
        f"        {hash_inputs}\n"
        "    )), 1, 16), 16, 10) AS DECIMAL(38, 0))) AS STRING), '0') AS HashTotal\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") source_fingerprint"
    )


def get_source_fingerprint(base_query, columns, query_label):
    fingerprint_started = time.perf_counter()
    fingerprint_rows = databricks_query(build_source_fingerprint_query(base_query, columns))
    row_count = 0
    hash_total = "0"
    if fingerprint_rows and fingerprint_rows[0]:
        row_count = int(fingerprint_rows[0][0]) if fingerprint_rows[0][0] not in (None, "") else 0
        hash_total = stringify_nullable(fingerprint_rows[0][1]) or "0"
    log_phase_duration(f"{query_label} source fingerprint", fingerprint_started, row_count)
    return {
        "row_count": row_count,
        "hash_total": hash_total,
    }


def get_sql_server_source_fingerprint(cursor, base_query, columns, query_label):
    fingerprint_started = time.perf_counter()
    cursor.execute(base_query)
    row_count = 0
    hash_total = 0
    while True:
        batch_rows = cursor.fetchmany(5000)
        if not batch_rows:
            break
        for row in batch_rows:
            row_count += 1
            digest_input = "||".join(
                [stringify_nullable(value) or "<NULL>" for value in row]
            )
            hash_total += int(hashlib.md5(digest_input.encode("utf-8")).hexdigest()[:16], 16)
    log_phase_duration(f"{query_label} source fingerprint", fingerprint_started, row_count)
    return {
        "row_count": row_count,
        "hash_total": str(hash_total),
    }


def build_limit_offset_query(base_query, order_by_clause, limit_value, offset_value):
    return (
        "SELECT *\n"
        "FROM (\n"
        f"{base_query.strip()}\n"
        ") paged_results\n"
        f"ORDER BY {order_by_clause}\n"
        f"LIMIT {limit_value} OFFSET {offset_value}"
    )


def get_databricks_batch_offsets(base_query, batch_size):
    count_started = time.perf_counter()
    count_rows = databricks_query(build_count_query(base_query))
    total_rows = int(count_rows[0][0]) if count_rows and count_rows[0] and count_rows[0][0] not in (None, "") else 0
    log_phase_duration("databricks count query", count_started, total_rows)
    return total_rows, list(range(0, total_rows, batch_size))


def fetch_parallel_databricks_batches(base_query, order_by_clause, batch_size, max_workers, query_label):
    offset_discovery_started = time.perf_counter()
    total_rows, offsets = get_databricks_batch_offsets(base_query, batch_size)
    log_phase_duration(f"{query_label} offset discovery", offset_discovery_started, total_rows)
    if total_rows == 0:
        logger.info("No rows found for %s", query_label)
        return []
    logger.info(
        "Fetching %s rows for %s in %s batches with workers=%s",
        total_rows,
        query_label,
        len(offsets),
        max_workers,
    )

    rows_by_offset = {}
    offset_fetch_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_offset = {
            executor.submit(
                databricks_query,
                build_limit_offset_query(base_query, order_by_clause, batch_size, offset),
            ): offset
            for offset in offsets
        }
        for future in as_completed(future_to_offset):
            offset = future_to_offset[future]
            batch_rows = future.result()
            rows_by_offset[offset] = batch_rows
            logger.info(
                "%s batch offset=%s fetched=%s",
                query_label,
                offset,
                len(batch_rows),
            )
    log_phase_duration(f"{query_label} offset fetch", offset_fetch_started, total_rows)

    ordered_rows = []
    for offset in offsets:
        ordered_rows.extend(rows_by_offset.get(offset, []))
    return ordered_rows


def build_prepared_churn_source_query(source_name):
    return (
        "SELECT\n"
        "    SubscriberAccountNumber,\n"
        "    ChurnProbability,\n"
        "    PredictionMonth,\n"
        "    Top1Feature,\n"
        "    Top2Feature,\n"
        "    Top3Feature\n"
        f"FROM {source_name}"
    )


def get_res_churn_source_query():
    if RES_CHURN_PREPARED_SOURCE:
        logger.info(
            "Using prepared Databricks source for residential churn: %s",
            RES_CHURN_PREPARED_SOURCE,
        )
        return build_prepared_churn_source_query(RES_CHURN_PREPARED_SOURCE)
    return RES_CHURN_QUERY


def get_com_churn_source_query():
    if COM_CHURN_PREPARED_SOURCE:
        logger.info(
            "Using prepared Databricks source for commercial churn: %s",
            COM_CHURN_PREPARED_SOURCE,
        )
        return build_prepared_churn_source_query(COM_CHURN_PREPARED_SOURCE)
    return COM_CHURN_QUERY


def build_prepared_call_source_query(source_name, columns):
    column_list = ",\n    ".join(columns)
    return (
        "SELECT\n"
        f"    {column_list}\n"
        f"FROM {source_name}"
    )


def get_call_monthly_agg_source_query():
    if CALL_MONTHLY_AGG_PREPARED_SOURCE:
        logger.info(
            "Using prepared Databricks source for call monthly aggregate: %s",
            CALL_MONTHLY_AGG_PREPARED_SOURCE,
        )
        return build_prepared_call_source_query(
            CALL_MONTHLY_AGG_PREPARED_SOURCE,
            [
                "AccountNumber",
                "CustomerType",
                "MonthStart",
                "ContactMonthStart",
                "NumberOfCalls",
                "AverageAgentTalkMin",
                "AverageTotalContactDurationMin",
                "TotalAgentTalkMin",
                "TotalContactDurationMin",
            ],
        )
    return CALL_MONTHLY_AGG_QUERY


def get_call_records_source_query():
    if CALL_RECORDS_MONTHLY_PREPARED_SOURCE:
        logger.info(
            "Using prepared Databricks source for call records monthly: %s",
            CALL_RECORDS_MONTHLY_PREPARED_SOURCE,
        )
        return build_prepared_call_source_query(
            CALL_RECORDS_MONTHLY_PREPARED_SOURCE,
            [
                "CustomerAccount",
                "SubscriberAccount",
                "CustomerType",
                "MonthStart",
                "NumberOfCalls",
                "TotalDurationMinutes",
                "AvgDurationMinutes",
                "LatestClientSentiment",
                "LatestIsResolved",
            ],
        )
    return CALL_RECORDS_MONTHLY_QUERY


def build_recent_month_starts(batch_count):
    current = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    for _ in range(batch_count):
        months.append(current)
        current = (current - timedelta(days=1)).replace(day=1)
    months.reverse()
    return months


def sql_connect(prefix):
    driver = os.getenv(f"{prefix}_DRIVER", "ODBC Driver 18 for SQL Server")
    server = os.environ[f"{prefix}_SERVER"]
    database = os.environ[f"{prefix}_DATABASE"]
    username = os.environ[f"{prefix}_USERNAME"]
    password = os.environ[f"{prefix}_PASSWORD"]
    escaped_password = password.replace("}", "}}")
    encrypt = os.getenv(f"{prefix}_ENCRYPT", "yes")
    trust = os.getenv(f"{prefix}_TRUST_SERVER_CERTIFICATE", "yes")

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={{{escaped_password}}};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
    )
    return pyodbc.connect(conn_str, autocommit=False)


def source_and_target_share_server():
    global SOURCE_AND_TARGET_SHARE_SERVER
    if SOURCE_AND_TARGET_SHARE_SERVER is None:
        SOURCE_AND_TARGET_SHARE_SERVER = (
            os.getenv("SOURCE_SQL_SERVER") == os.getenv("TARGET_SQL_SERVER")
        )
    return SOURCE_AND_TARGET_SHARE_SERVER


def truncate_and_load(cursor, table_name, insert_sql, rows):
    cursor.execute(f"TRUNCATE TABLE {table_name}")
    append_rows(cursor, insert_sql, rows)


def make_stage_table_name(target_table):
    return f"#stage_{target_table.split('.')[-1]}"


def make_shared_stage_table_name(target_table, worker_index=None):
    table_suffix = target_table.split('.')[-1]
    unique_suffix = uuid.uuid4().hex[:8]
    if worker_index is None:
        return f"##stage_{table_suffix}_{unique_suffix}"
    return f"##stage_{table_suffix}_w{worker_index}_{unique_suffix}"


def create_stage_table(cursor, target_table, stage_table):
    cursor.execute(
        f"IF OBJECT_ID('tempdb..{stage_table}') IS NOT NULL DROP TABLE {stage_table}"
    )
    cursor.execute(f"SELECT TOP 0 * INTO {stage_table} FROM {target_table}")


def create_shared_stage_table(cursor, target_table, stage_table):
    cursor.execute(
        f"IF OBJECT_ID('tempdb..{stage_table}') IS NOT NULL DROP TABLE {stage_table}"
    )
    cursor.execute(f"SELECT TOP 0 * INTO {stage_table} FROM {target_table}")


def drop_stage_table(cursor, stage_table):
    if stage_table.startswith("#"):
        cursor.execute(
            f"IF OBJECT_ID('tempdb..{stage_table}') IS NOT NULL DROP TABLE {stage_table}"
        )
    else:
        cursor.execute(
            f"IF OBJECT_ID('{stage_table}', 'U') IS NOT NULL DROP TABLE {stage_table}"
        )


def build_insert_sql(table_name, columns):
    placeholders = ", ".join(["?"] * len(columns))
    column_list = ", ".join(columns)
    return (
        f"INSERT INTO {table_name}\n"
        f"({column_list})\n"
        f"VALUES ({placeholders})"
    )


def replace_table_from_stage(connection, cursor, target_table, stage_table):
    try:
        cursor.execute(f"TRUNCATE TABLE {target_table}")
        cursor.execute(f"INSERT INTO {target_table} SELECT * FROM {stage_table}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def replace_table_from_stage_tables(connection, cursor, target_table, stage_tables):
    if not stage_tables:
        return
    if len(stage_tables) == 1:
        replace_table_from_stage(connection, cursor, target_table, stage_tables[0])
        return

    stage_union_query = "\nUNION ALL\n".join(
        [f"SELECT * FROM {stage_table}" for stage_table in stage_tables]
    )
    try:
        cursor.execute(f"TRUNCATE TABLE {target_table}")
        cursor.execute(
            f"""
            INSERT INTO {target_table}
            SELECT *
            FROM (
                {stage_union_query}
            ) AS stage
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def replace_table_slice_from_stage(connection, cursor, target_table, stage_table, slice_columns):
    join_predicate = " AND ".join(
        [f"target.{column} = stage.{column}" for column in slice_columns]
    )
    try:
        cursor.execute(
            f"""
            DELETE target
            FROM {target_table} AS target
            WHERE EXISTS (
                SELECT 1
                FROM {stage_table} AS stage
                WHERE {join_predicate}
            )
            """
        )
        cursor.execute(f"INSERT INTO {target_table} SELECT * FROM {stage_table}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def replace_table_slice_from_stage_tables(connection, cursor, target_table, stage_tables, slice_columns):
    if not stage_tables:
        return
    if len(stage_tables) == 1:
        replace_table_slice_from_stage(connection, cursor, target_table, stage_tables[0], slice_columns)
        return

    stage_union_query = "\nUNION ALL\n".join(
        [f"SELECT * FROM {stage_table}" for stage_table in stage_tables]
    )
    join_predicate = " AND ".join(
        [f"target.{column} = stage.{column}" for column in slice_columns]
    )
    try:
        cursor.execute(
            f"""
            DELETE target
            FROM {target_table} AS target
            WHERE EXISTS (
                SELECT 1
                FROM (
                    {stage_union_query}
                ) AS stage
                WHERE {join_predicate}
            )
            """
        )
        cursor.execute(
            f"""
            INSERT INTO {target_table}
            SELECT *
            FROM (
                {stage_union_query}
            ) AS stage
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def sync_table_slice_from_stage_query(
    connection,
    cursor,
    target_table,
    stage_query,
    slice_columns,
    key_columns,
    compare_columns,
):
    stage_source = f"({stage_query}) AS stage"
    slice_source = f"({stage_query}) AS slice_stage"
    key_predicate = " AND ".join(
        [f"target.{column} = stage.{column}" for column in key_columns]
    )
    slice_predicate = " AND ".join(
        [f"target.{column} = slice_stage.{column}" for column in slice_columns]
    )
    update_assignments = ", ".join(
        [f"target.{column} = stage.{column}" for column in compare_columns + ["RefreshedAt"]]
    )
    compare_target_columns = ", ".join([f"target.{column}" for column in compare_columns])
    compare_stage_columns = ", ".join([f"stage.{column}" for column in compare_columns])

    try:
        cursor.execute(
            f"""
            DELETE target
            FROM {target_table} AS target
            WHERE EXISTS (
                SELECT 1
                FROM {slice_source}
                WHERE {slice_predicate}
            )
              AND NOT EXISTS (
                SELECT 1
                FROM {stage_source}
                WHERE {key_predicate}
            )
            """
        )
        deleted_rows = cursor.rowcount

        cursor.execute(
            f"""
            UPDATE target
            SET {update_assignments}
            FROM {target_table} AS target
            INNER JOIN {stage_source}
                ON {key_predicate}
            WHERE EXISTS (
                SELECT {compare_target_columns}
                EXCEPT
                SELECT {compare_stage_columns}
            )
            """
        )
        updated_rows = cursor.rowcount

        cursor.execute(
            f"""
            INSERT INTO {target_table}
            SELECT *
            FROM {stage_source}
            WHERE NOT EXISTS (
                SELECT 1
                FROM {target_table} AS target
                WHERE {key_predicate}
            )
            """
        )
        inserted_rows = cursor.rowcount
        connection.commit()
        return {
            "deleted_rows": deleted_rows,
            "updated_rows": updated_rows,
            "inserted_rows": inserted_rows,
        }
    except Exception:
        connection.rollback()
        raise


def sync_table_slice_from_stage(
    connection,
    cursor,
    target_table,
    stage_table,
    slice_columns,
    key_columns,
    compare_columns,
):
    return sync_table_slice_from_stage_query(
        connection,
        cursor,
        target_table,
        f"SELECT * FROM {stage_table}",
        slice_columns,
        key_columns,
        compare_columns,
    )


def sync_table_slice_from_stage_tables(
    connection,
    cursor,
    target_table,
    stage_tables,
    slice_columns,
    key_columns,
    compare_columns,
):
    if not stage_tables:
        return {"deleted_rows": 0, "updated_rows": 0, "inserted_rows": 0}
    if len(stage_tables) == 1:
        return sync_table_slice_from_stage(
            connection,
            cursor,
            target_table,
            stage_tables[0],
            slice_columns,
            key_columns,
            compare_columns,
        )
    return sync_table_slice_from_stage_query(
        connection,
        cursor,
        target_table,
        "\nUNION ALL\n".join([f"SELECT * FROM {stage_table}" for stage_table in stage_tables]),
        slice_columns,
        key_columns,
        compare_columns,
    )


def load_modem_stage_from_sql_server(cursor, stage_table):
    cursor.execute(
        f"""
        ;WITH ranked_modems AS (
            SELECT
                UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', '')) AS ModemMac,
                ip AS IP,
                tstamp AS LastSeen,
                usint AS USINT,
                CASE
                    WHEN LOWER(LTRIM(RTRIM(CONVERT(VARCHAR(255), state)))) IN ('online', 'offline')
                        THEN UPPER(LEFT(CONVERT(VARCHAR(255), state), 1)) + LOWER(SUBSTRING(CONVERT(VARCHAR(255), state), 2, 255))
                    WHEN ip IS NULL OR ip = '0.0.0.0' THEN 'Offline'
                    ELSE 'Online'
                END AS Status,
                state AS State,
                usrxlvl AS USRXLVL,
                ustxpwr AS USTXPWR,
                usrxsnr AS USRXSNR,
                dsrxlvl AS DSRXLVL,
                dsrxsnr AS DSRXSNR,
                dsprefec AS DSPREFEC,
                dspostfec AS DSPOSTFEC,
                dsbw AS DSBW,
                usbw AS USBW,
                fibernode AS FiberNode,
                cmts AS CMTS,
                ROW_NUMBER() OVER (
                    PARTITION BY UPPER(REPLACE(LTRIM(RTRIM(CONVERT(VARCHAR(255), mac))), ':', ''))
                    ORDER BY tstamp DESC
                ) AS row_rank
            FROM newbacondata.dbo.cmdata2011
            WHERE ip <> '0.0.0.0'
        )
        INSERT INTO {stage_table}
        (
            ModemMac, IP, LastSeen, USINT, Status, State, USRXLVL, USTXPWR, USRXSNR,
            DSRXLVL, DSRXSNR, DSPREFEC, DSPOSTFEC, DSBW, USBW, FiberNode, CMTS, RefreshedAt
        )
        SELECT
            ModemMac, IP, LastSeen, USINT, Status, State, USRXLVL, USTXPWR, USRXSNR,
            DSRXLVL, DSRXSNR, DSPREFEC, DSPOSTFEC, DSBW, USBW, FiberNode, CMTS, SYSUTCDATETIME()
        FROM ranked_modems
        WHERE row_rank = 1
        """
    )


def get_table_row_count(cursor, table_name):
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return int(cursor.fetchone()[0])


def reconnect_target(connection):
    try:
        connection.close()
    except Exception:
        pass
    new_connection = sql_connect("TARGET_SQL")
    return new_connection, new_connection.cursor()


def append_rows(cursor, insert_sql, rows):
    append_rows_with_mode(cursor, insert_sql, rows)


def append_rows_with_mode(
    cursor,
    insert_sql,
    rows,
    use_fast_executemany=True,
    chunk_size=SQL_INSERT_CHUNK_SIZE,
    commit_connection=None,
    commit_every_chunks=1,
    progress_label=None,
    progress_log_every=None,
    batch_timing_label=None,
):
    if not rows:
        return

    inserted_count = 0
    chunks_since_commit = 0
    for start_index in range(0, len(rows), chunk_size):
        chunk = rows[start_index:start_index + chunk_size]
        cursor.fast_executemany = use_fast_executemany
        execute_started = time.perf_counter()
        cursor.executemany(insert_sql, chunk)
        execute_elapsed = time.perf_counter() - execute_started
        inserted_count += len(chunk)
        chunks_since_commit += 1
        commit_elapsed = 0.0
        committed = False
        if commit_connection is not None:
            if chunks_since_commit >= commit_every_chunks:
                commit_started = time.perf_counter()
                commit_connection.commit()
                commit_elapsed = time.perf_counter() - commit_started
                chunks_since_commit = 0
                committed = True
        if batch_timing_label:
            logger.info(
                "%s batch rows=%s inserted=%s/%s executemany=%.2fs commit=%.2fs committed=%s",
                batch_timing_label,
                len(chunk),
                inserted_count,
                len(rows),
                execute_elapsed,
                commit_elapsed,
                committed,
            )
        if progress_label and progress_log_every and inserted_count % progress_log_every == 0:
            logger.info("%s progress=%s/%s", progress_label, inserted_count, len(rows))

    if commit_connection is not None and chunks_since_commit:
        final_commit_started = time.perf_counter()
        commit_connection.commit()
        if batch_timing_label:
            logger.info(
                "%s final commit pending_chunks=%s commit=%.2fs",
                batch_timing_label,
                chunks_since_commit,
                time.perf_counter() - final_commit_started,
            )

    if progress_label and inserted_count % progress_log_every != 0:
        logger.info("%s progress=%s/%s", progress_label, inserted_count, len(rows))


def parse_databricks_timestamp(value):
    if value in (None, ""):
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_call_monthly_rows(rows):
    normalized = []
    for row in rows:
        normalized.append(
            (
                row[0],
                row[1],
                parse_databricks_timestamp(row[2]).date() if row[2] else None,
                parse_databricks_timestamp(row[3]).date() if row[3] else None,
                int(row[4]) if row[4] not in (None, "") else None,
                Decimal(row[5]) if row[5] not in (None, "") else None,
                Decimal(row[6]) if row[6] not in (None, "") else None,
                Decimal(row[7]) if row[7] not in (None, "") else None,
                Decimal(row[8]) if row[8] not in (None, "") else None,
            )
        )
    return normalized


def normalize_churn_rows(rows):
    normalized = []
    for row in rows:
        normalized.append(
            (
                stringify_nullable(row[0]),
                float(row[1]) if row[1] not in (None, "") else None,
                stringify_nullable(row[2]),
                stringify_nullable(row[3]),
                stringify_nullable(row[4]),
                stringify_nullable(row[5]),
            )
        )
    return normalized


def normalize_call_records_rows(rows):
    normalized = []
    for row in rows:
        normalized.append(
            (
                row[0],
                row[1],
                row[2],
                parse_databricks_timestamp(row[3]).date() if row[3] else None,
                int(row[4]) if row[4] not in (None, "") else None,
                Decimal(row[5]) if row[5] not in (None, "") else None,
                Decimal(row[6]) if row[6] not in (None, "") else None,
                stringify_nullable(row[7]) if len(row) > 7 else "UNKNOWN",
                row[8] == "true" if len(row) > 8 and row[8] not in (None, "") else False,
            )
        )
    return normalized


def stringify_nullable(value):
    if value is None:
        return None
    return str(value).strip()


def normalize_modem_rows(rows):
    normalized = []
    for row in rows:
        normalized.append(
            (
                stringify_nullable(row[0]),
                stringify_nullable(row[1]),
                row[2],
                stringify_nullable(row[3]),
                stringify_nullable(row[4]),
                stringify_nullable(row[5]),
                stringify_nullable(row[6]),
                stringify_nullable(row[7]),
                stringify_nullable(row[8]),
                stringify_nullable(row[9]),
                stringify_nullable(row[10]),
                stringify_nullable(row[11]),
                stringify_nullable(row[12]),
                stringify_nullable(row[13]),
                stringify_nullable(row[14]),
                stringify_nullable(row[15]),
                stringify_nullable(row[16]),
            )
        )
    return normalized


def safe_rollback(connection):
    try:
        connection.rollback()
    except pyodbc.Error as exc:
        logger.warning("Rollback failed on broken SQL connection: %s", exc)


def load_checkpoint_state():
    if not os.path.exists(CHECKPOINT_FILE):
        return {"completed_tables": []}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        completed_tables = payload.get("completed_tables", [])
        if isinstance(completed_tables, list):
            return {"completed_tables": completed_tables}
    except Exception as exc:
        logger.warning("Unable to read checkpoint file %s: %s", CHECKPOINT_FILE, exc)
    return {"completed_tables": []}


def load_source_state():
    if not os.path.exists(SOURCE_STATE_FILE):
        return {"source_fingerprints": {}}
    try:
        with open(SOURCE_STATE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        source_fingerprints = payload.get("source_fingerprints", {})
        if isinstance(source_fingerprints, dict):
            return {"source_fingerprints": source_fingerprints}
    except Exception as exc:
        logger.warning("Unable to read source state file %s: %s", SOURCE_STATE_FILE, exc)
    return {"source_fingerprints": {}}


def save_checkpoint_state(state):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def save_source_state(state):
    with open(SOURCE_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def mark_table_completed(state, table_name):
    if table_name not in state["completed_tables"]:
        state["completed_tables"].append(table_name)
        save_checkpoint_state(state)


def mark_source_fingerprint(state, table_name, fingerprint):
    state["source_fingerprints"][table_name] = fingerprint
    save_source_state(state)


def reset_checkpoint_state():
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


def should_skip_table(state, table_name):
    if RUN_ONLY_TABLES is not None and table_name not in RUN_ONLY_TABLES:
        return True
    if table_name in SKIP_TABLES:
        return True
    if not RESUME_PARTIAL_RUNS:
        return False
    return table_name in state.get("completed_tables", [])


def table_has_any_rows(cursor, table_name):
    cursor.execute(f"SELECT TOP 1 1 FROM {table_name}")
    return cursor.fetchone() is not None


def should_skip_unchanged_source(cursor, source_state, table_name, fingerprint):
    previous_fingerprint = source_state.get("source_fingerprints", {}).get(table_name)
    if previous_fingerprint != fingerprint:
        return False
    if not table_has_any_rows(cursor, table_name):
        return False
    logger.info("Skipping unchanged source table=%s fingerprint=%s", table_name, fingerprint)
    return True


def write_refresh_audit(status, message):
    try:
        audit_conn = sql_connect("TARGET_SQL")
        try:
            cursor = audit_conn.cursor()
            cursor.execute(
                """
                INSERT INTO dbo.service_churn_refresh_audit
                (RefreshStartedAt, RefreshCompletedAt, RefreshStatus, RefreshMessage)
                VALUES (?, ?, ?, ?)
                """,
                datetime.utcnow(), datetime.utcnow(), status, message[:1000]
            )
            audit_conn.commit()
        finally:
            audit_conn.close()
    except Exception as exc:
        logger.warning("Unable to write refresh audit row: %s", exc)


def log_table_refresh(table_name, row_count):
    logger.info("Loaded %s rows into %s", row_count, table_name)


def stamp_table_refreshed_at(connection, cursor, table_name):
    """Stamp all rows with the latest refresh timestamp after table sync completes."""
    cursor.execute(
        """
        SELECT
            CASE
                WHEN COL_LENGTH(?, 'Refreshed At') IS NOT NULL THEN 1
                WHEN COL_LENGTH(?, 'RefreshedAt') IS NOT NULL THEN 2
                ELSE 0
            END
        """,
        table_name,
        table_name,
    )
    column_selector = int(cursor.fetchone()[0])

    if column_selector == 1:
        cursor.execute(f"UPDATE {table_name} SET [Refreshed At] = SYSUTCDATETIME()")
        connection.commit()
        logger.info("Updated refresh timestamp column=[Refreshed At] table=%s rows=%s", table_name, cursor.rowcount)
    elif column_selector == 2:
        cursor.execute(f"UPDATE {table_name} SET RefreshedAt = SYSUTCDATETIME()")
        connection.commit()
        logger.info("Updated refresh timestamp column=RefreshedAt table=%s rows=%s", table_name, cursor.rowcount)
    else:
        logger.warning("Skipped refresh timestamp update; no refresh timestamp column found table=%s", table_name)


def log_databricks_source_mode(table_name, prepared_source_name):
    if prepared_source_name:
        logger.info(
            "Source mode table=%s type=prepared source=%s",
            table_name,
            prepared_source_name,
        )
    else:
        logger.info("Source mode table=%s type=raw", table_name)


def log_phase_duration(phase_label, started_at, row_count=None):
    elapsed_seconds = time.perf_counter() - started_at
    if row_count is None:
        logger.info("%s completed in %.2fs", phase_label, elapsed_seconds)
    else:
        logger.info("%s completed in %.2fs rows=%s", phase_label, elapsed_seconds, row_count)
    return elapsed_seconds


def log_churn_refresh_summary(table_name, source_mode, row_count, fetch_seconds, insert_seconds, replace_seconds):
    logger.info(
        "Churn summary table=%s source_mode=%s rows=%s fetch=%.2fs insert=%.2fs replace=%.2fs total=%.2fs",
        table_name,
        source_mode,
        row_count,
        fetch_seconds,
        insert_seconds,
        replace_seconds,
        fetch_seconds + insert_seconds + replace_seconds,
    )


def log_churn_sync_summary(table_name, deleted_rows, updated_rows, inserted_rows):
    logger.info(
        "Churn sync table=%s deleted=%s updated=%s inserted=%s",
        table_name,
        deleted_rows,
        updated_rows,
        inserted_rows,
    )


def log_table_sync_summary(table_name, deleted_rows, updated_rows, inserted_rows):
    logger.info(
        "Table sync table=%s deleted=%s updated=%s inserted=%s",
        table_name,
        deleted_rows,
        updated_rows,
        inserted_rows,
    )


def refresh_once():
    refresh_started = time.perf_counter()
    target = sql_connect("TARGET_SQL")
    source_sql = None
    month_batches = build_recent_month_starts(CALL_MONTH_BATCHES)
    checkpoint_state = load_checkpoint_state()
    source_state = load_source_state()
    truckroll_fetch_future = None
    account_mac_fetch_future = None
    modem_fetch_future = None
    res_fetch_future = None
    com_fetch_future = None
    call_monthly_fetch_future = None
    call_records_fetch_future = None

    try:
        target_cursor = target.cursor()
        if RUN_ONLY_TABLES is not None:
            logger.info("Run scope run_only_tables=%s", sorted(RUN_ONLY_TABLES))
        if SKIP_TABLES:
            logger.info("Run scope skip_tables=%s", sorted(SKIP_TABLES))
        res_source_query = get_res_churn_source_query()
        com_source_query = get_com_churn_source_query()
        call_monthly_source_query = get_call_monthly_agg_source_query()
        call_records_source_query = get_call_records_source_query()
        log_databricks_source_mode("dbo.service_churn_res_latest", RES_CHURN_PREPARED_SOURCE)
        log_databricks_source_mode("dbo.service_churn_com_latest", COM_CHURN_PREPARED_SOURCE)
        log_databricks_source_mode("dbo.service_churn_call_monthly_agg", CALL_MONTHLY_AGG_PREPARED_SOURCE)
        log_databricks_source_mode("dbo.service_churn_call_records_monthly", CALL_RECORDS_MONTHLY_PREPARED_SOURCE)

        truckroll_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_truckroll_base")
        account_mac_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_account_mac_map")
        modem_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_modem_health_latest")
        res_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_res_latest")
        com_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_com_latest")
        call_monthly_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_call_monthly_agg")
        call_records_skipped = should_skip_table(checkpoint_state, "dbo.service_churn_call_records_monthly")
        share_modem_server = source_and_target_share_server()

        truckroll_source_fingerprint = None
        if not truckroll_skipped:
            truckroll_source_fingerprint = get_source_fingerprint(
                TRUCKROLL_QUERY,
                ["LegacyAccountNumber", "SubscriberAccountNumber", "PhoneNumber", "BillingCity"],
                "truckroll",
            )
            truckroll_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_truckroll_base",
                truckroll_source_fingerprint,
            )

        account_mac_source_fingerprint = None
        if not account_mac_skipped:
            account_mac_source_fingerprint = get_source_fingerprint(
                ACCOUNT_MAC_QUERY,
                ["AccountNumber", "ModemMac"],
                "account mac",
            )
            account_mac_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_account_mac_map",
                account_mac_source_fingerprint,
            )

        modem_source_fingerprint = None
        if not modem_skipped:
            if share_modem_server:
                modem_source_fingerprint = get_sql_server_source_fingerprint(
                    target_cursor,
                    SOURCE_MODEM_QUERY,
                    [
                        "ModemMac", "IP", "LastSeen", "USINT", "Status", "State", "USRXLVL", "USTXPWR", "USRXSNR",
                        "DSRXLVL", "DSRXSNR", "DSPREFEC", "DSPOSTFEC", "DSBW", "USBW", "FiberNode", "CMTS",
                    ],
                    "modem health",
                )
            else:
                if source_sql is None:
                    source_sql = sql_connect("SOURCE_SQL")
                modem_source_fingerprint = get_sql_server_source_fingerprint(
                    source_sql.cursor(),
                    SOURCE_MODEM_QUERY,
                    [
                        "ModemMac", "IP", "LastSeen", "USINT", "Status", "State", "USRXLVL", "USTXPWR", "USRXSNR",
                        "DSRXLVL", "DSRXSNR", "DSPREFEC", "DSPOSTFEC", "DSBW", "USBW", "FiberNode", "CMTS",
                    ],
                    "modem health",
                )
            modem_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_modem_health_latest",
                modem_source_fingerprint,
            )

        res_source_fingerprint = None
        if not res_skipped:
            res_source_fingerprint = get_source_fingerprint(
                res_source_query,
                ["SubscriberAccountNumber", "ChurnProbability", "PredictionMonth", "Top1Feature", "Top2Feature", "Top3Feature"],
                "residential churn",
            )
            res_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_res_latest",
                res_source_fingerprint,
            )

        com_source_fingerprint = None
        if not com_skipped:
            com_source_fingerprint = get_source_fingerprint(
                com_source_query,
                ["SubscriberAccountNumber", "ChurnProbability", "PredictionMonth", "Top1Feature", "Top2Feature", "Top3Feature"],
                "commercial churn",
            )
            com_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_com_latest",
                com_source_fingerprint,
            )

        call_monthly_source_fingerprint = None
        if not call_monthly_skipped:
            call_monthly_source_fingerprint = get_source_fingerprint(
                call_monthly_source_query,
                [
                    "AccountNumber", "CustomerType", "MonthStart", "ContactMonthStart", "NumberOfCalls",
                    "AverageAgentTalkMin", "AverageTotalContactDurationMin", "TotalAgentTalkMin", "TotalContactDurationMin",
                ],
                "call monthly aggregate",
            )
            call_monthly_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_call_monthly_agg",
                call_monthly_source_fingerprint,
            )

        call_records_source_fingerprint = None
        if not call_records_skipped:
            call_records_source_fingerprint = get_source_fingerprint(
                call_records_source_query,
                [
                    "CustomerAccount", "SubscriberAccount", "CustomerType", "MonthStart", "NumberOfCalls",
                    "TotalDurationMinutes", "AvgDurationMinutes",
                ],
                "call records monthly",
            )
            call_records_skipped = should_skip_unchanged_source(
                target_cursor,
                source_state,
                "dbo.service_churn_call_records_monthly",
                call_records_source_fingerprint,
            )

        with ThreadPoolExecutor(max_workers=TOP_LEVEL_DATA_FETCH_WORKERS) as fetch_executor:
            if not truckroll_skipped:
                logger.info("Scheduling truckroll fetch")
                truckroll_fetch_future = fetch_executor.submit(fetch_truckroll_rows)
            if not account_mac_skipped:
                logger.info("Scheduling account mac fetch")
                account_mac_fetch_future = fetch_executor.submit(fetch_account_mac_rows)
            if not modem_skipped and not share_modem_server:
                logger.info("Scheduling modem health fetch")
                modem_fetch_future = fetch_executor.submit(fetch_modem_rows)
            if not res_skipped and RES_CHURN_PREPARED_SOURCE:
                logger.info("Scheduling residential churn fetch")
                res_fetch_future = fetch_executor.submit(fetch_residential_churn_rows)
            if not com_skipped and COM_CHURN_PREPARED_SOURCE:
                logger.info("Scheduling commercial churn fetch")
                com_fetch_future = fetch_executor.submit(fetch_commercial_churn_rows)
            if not call_monthly_skipped:
                logger.info("Scheduling call monthly aggregate fetch")
                call_monthly_fetch_future = fetch_executor.submit(fetch_call_monthly_agg_rows, month_batches)
            if not call_records_skipped:
                logger.info("Scheduling call records monthly fetch")
                call_records_fetch_future = fetch_executor.submit(fetch_call_records_monthly_rows, month_batches)

            if truckroll_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_truckroll_base")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_truckroll_base")
                truckroll_stage = make_stage_table_name("dbo.service_churn_truckroll_base")
                create_stage_table(target_cursor, "dbo.service_churn_truckroll_base", truckroll_stage)
                truckroll_fetch_wait_started = time.perf_counter()
                truckroll_rows = truckroll_fetch_future.result()
                log_phase_duration("truckroll fetch wait", truckroll_fetch_wait_started, len(truckroll_rows))
                append_rows_with_mode(
                    target_cursor,
                    build_insert_sql(
                        truckroll_stage,
                        ["LegacyAccountNumber", "SubscriberAccountNumber", "PhoneNumber", "BillingCity", "RefreshedAt"],
                    ).replace(
                        "VALUES (?, ?, ?, ?, ?)",
                        "VALUES (?, ?, ?, ?, SYSUTCDATETIME())",
                    ),
                    truckroll_rows,
                    commit_connection=target,
                )
                replace_table_from_stage(target, target_cursor, "dbo.service_churn_truckroll_base", truckroll_stage)
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_truckroll_base")
                log_table_refresh("dbo.service_churn_truckroll_base", len(truckroll_rows))
                mark_table_completed(checkpoint_state, "dbo.service_churn_truckroll_base")
                if truckroll_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_truckroll_base", truckroll_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if account_mac_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_account_mac_map")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_account_mac_map")
                account_mac_stage = make_stage_table_name("dbo.service_churn_account_mac_map")
                create_stage_table(target_cursor, "dbo.service_churn_account_mac_map", account_mac_stage)
                account_mac_fetch_wait_started = time.perf_counter()
                account_mac_rows = account_mac_fetch_future.result()
                log_phase_duration("account mac fetch wait", account_mac_fetch_wait_started, len(account_mac_rows))
                append_rows_with_mode(
                    target_cursor,
                    build_insert_sql(
                        account_mac_stage,
                        ["AccountNumber", "ModemMac", "RefreshedAt"],
                    ).replace(
                        "VALUES (?, ?, ?)",
                        "VALUES (?, ?, SYSUTCDATETIME())",
                    ),
                    account_mac_rows,
                    commit_connection=target,
                )
                replace_table_from_stage(target, target_cursor, "dbo.service_churn_account_mac_map", account_mac_stage)
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_account_mac_map")
                log_table_refresh("dbo.service_churn_account_mac_map", len(account_mac_rows))
                mark_table_completed(checkpoint_state, "dbo.service_churn_account_mac_map")
                if account_mac_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_account_mac_map", account_mac_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if modem_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_modem_health_latest")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_modem_health_latest")
                modem_stage = make_stage_table_name("dbo.service_churn_modem_health_latest")
                create_stage_table(target_cursor, "dbo.service_churn_modem_health_latest", modem_stage)
                if share_modem_server:
                    logger.info("Loading modem health with SQL-side insert-select")
                    modem_stage_load_started = time.perf_counter()
                    load_modem_stage_from_sql_server(target_cursor, modem_stage)
                    target.commit()
                    modem_total_rows = get_table_row_count(target_cursor, modem_stage)
                    log_phase_duration("modem health stage load", modem_stage_load_started, modem_total_rows)
                else:
                    modem_fetch_wait_started = time.perf_counter()
                    normalized_modem_rows = modem_fetch_future.result()
                    modem_total_rows = len(normalized_modem_rows)
                    log_phase_duration("modem health fetch wait", modem_fetch_wait_started, modem_total_rows)
                    modem_stage_insert_started = time.perf_counter()
                    modem_stage_handles = []
                    try:
                        modem_stage_handles, modem_total_rows = load_parallel_modem_rows_to_stage_tables(
                            "dbo.service_churn_modem_health_latest",
                            normalized_modem_rows,
                        )
                        modem_stage_tables = [stage_table for stage_table, _, _ in modem_stage_handles]
                    except pyodbc.Error as exc:
                        cleanup_parallel_stage_tables(modem_stage_handles)
                        modem_stage_handles = []
                        logger.warning(
                            "Parallel modem stage load failed: %s; falling back to single-stage insert",
                            exc,
                        )
                        modem_insert_sql = build_insert_sql(
                            modem_stage,
                            [
                                "ModemMac", "IP", "LastSeen", "USINT", "Status", "State", "USRXLVL", "USTXPWR", "USRXSNR",
                                "DSRXLVL", "DSRXSNR", "DSPREFEC", "DSPOSTFEC", "DSBW", "USBW", "FiberNode", "CMTS", "RefreshedAt",
                            ],
                        ).replace(
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
                        )
                        append_rows_with_mode(
                            target_cursor,
                            modem_insert_sql,
                            normalized_modem_rows,
                            use_fast_executemany=False,
                            chunk_size=MODEM_INSERT_CHUNK_SIZE,
                            commit_connection=target,
                            commit_every_chunks=MODEM_INSERT_COMMIT_EVERY_CHUNKS,
                            progress_label="modem health",
                            progress_log_every=MODEM_PROGRESS_LOG_EVERY,
                        )
                        modem_stage_tables = [modem_stage]
                    log_phase_duration("modem health stage insert", modem_stage_insert_started, modem_total_rows)
                modem_replace_started = time.perf_counter()
                if share_modem_server:
                    replace_table_from_stage(target, target_cursor, "dbo.service_churn_modem_health_latest", modem_stage)
                else:
                    try:
                        replace_table_from_stage_tables(
                            target,
                            target_cursor,
                            "dbo.service_churn_modem_health_latest",
                            modem_stage_tables,
                        )
                    finally:
                        cleanup_parallel_stage_tables(modem_stage_handles)
                log_phase_duration("modem health target replace", modem_replace_started, modem_total_rows)
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_modem_health_latest")
                log_table_refresh("dbo.service_churn_modem_health_latest", modem_total_rows)
                mark_table_completed(checkpoint_state, "dbo.service_churn_modem_health_latest")
                if modem_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_modem_health_latest", modem_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if res_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_res_latest")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_res_latest")
                res_source_mode = "prepared" if RES_CHURN_PREPARED_SOURCE else "raw"
                res_insert_columns = ["SubscriberAccountNumber", "ChurnProbability", "PredictionMonth", "Top1Feature", "Top2Feature", "Top3Feature", "RefreshedAt"]
                res_sync_key_columns = ["SubscriberAccountNumber", "PredictionMonth"]
                res_sync_compare_columns = ["ChurnProbability", "Top1Feature", "Top2Feature", "Top3Feature"]
                res_fetch_seconds = 0.0
                if RES_CHURN_PREPARED_SOURCE:
                    res_stage = make_stage_table_name("dbo.service_churn_res_latest")
                    create_stage_table(target_cursor, "dbo.service_churn_res_latest", res_stage)
                    res_insert_sql = build_insert_sql(
                        res_stage,
                        res_insert_columns,
                    ).replace(
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        "VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
                    )
                    res_fetch_wait_started = time.perf_counter()
                    normalized_res_rows = res_fetch_future.result()
                    res_total_rows = len(normalized_res_rows)
                    res_fetch_seconds = log_phase_duration("residential churn fetch wait", res_fetch_wait_started, res_total_rows)
                    res_insert_started = time.perf_counter()
                    append_rows_with_mode(
                        target_cursor,
                        res_insert_sql,
                        normalized_res_rows,
                        use_fast_executemany=False,
                        chunk_size=RES_CHURN_INSERT_CHUNK_SIZE,
                        commit_connection=target,
                        commit_every_chunks=CHURN_STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                        progress_label="residential churn",
                        progress_log_every=CHURN_PROGRESS_LOG_EVERY,
                        batch_timing_label="residential churn insert",
                    )
                    res_insert_seconds = log_phase_duration("residential churn stage insert", res_insert_started, res_total_rows)
                    res_replace_started = time.perf_counter()
                    res_sync_counts = sync_table_slice_from_stage(
                        target,
                        target_cursor,
                        "dbo.service_churn_res_latest",
                        res_stage,
                        ["PredictionMonth"],
                        res_sync_key_columns,
                        res_sync_compare_columns,
                    )
                    res_replace_seconds = log_phase_duration("residential churn target replace", res_replace_started, res_total_rows)
                else:
                    res_batch_details_started = time.perf_counter()
                    res_batch_details = get_churn_batch_details(res_source_query)
                    res_fetch_seconds = log_phase_duration("residential churn batch discovery", res_batch_details_started, len(res_batch_details))
                    res_insert_started = time.perf_counter()
                    res_stage_handles = []
                    try:
                        res_stage_handles, res_total_rows = load_parallel_churn_stage_tables(
                            "dbo.service_churn_res_latest",
                            res_insert_columns,
                            res_source_query,
                            res_batch_details,
                            "residential churn",
                            CHURN_STAGE_WORKERS,
                        )
                        res_insert_seconds = log_phase_duration("residential churn parallel stage load", res_insert_started, res_total_rows)
                        res_replace_started = time.perf_counter()
                        res_sync_counts = sync_table_slice_from_stage_tables(
                            target,
                            target_cursor,
                            "dbo.service_churn_res_latest",
                            [stage_table for stage_table, _, _ in res_stage_handles],
                            ["PredictionMonth"],
                            res_sync_key_columns,
                            res_sync_compare_columns,
                        )
                        res_replace_seconds = log_phase_duration("residential churn target replace", res_replace_started, res_total_rows)
                    finally:
                        cleanup_parallel_stage_tables(res_stage_handles)
                target.commit()
                log_churn_sync_summary(
                    "dbo.service_churn_res_latest",
                    res_sync_counts["deleted_rows"],
                    res_sync_counts["updated_rows"],
                    res_sync_counts["inserted_rows"],
                )
                log_churn_refresh_summary(
                    "dbo.service_churn_res_latest",
                    res_source_mode,
                    res_total_rows,
                    res_fetch_seconds,
                    res_insert_seconds,
                    res_replace_seconds,
                )
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_res_latest")
                log_table_refresh("dbo.service_churn_res_latest", res_total_rows)
                mark_table_completed(checkpoint_state, "dbo.service_churn_res_latest")
                if res_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_res_latest", res_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if com_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_com_latest")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_com_latest")
                com_source_mode = "prepared" if COM_CHURN_PREPARED_SOURCE else "raw"
                com_insert_columns = ["SubscriberAccountNumber", "ChurnProbability", "PredictionMonth", "Top1Feature", "Top2Feature", "Top3Feature", "RefreshedAt"]
                com_sync_key_columns = ["SubscriberAccountNumber", "PredictionMonth"]
                com_sync_compare_columns = ["ChurnProbability", "Top1Feature", "Top2Feature", "Top3Feature"]
                com_fetch_seconds = 0.0
                if COM_CHURN_PREPARED_SOURCE:
                    com_stage = make_stage_table_name("dbo.service_churn_com_latest")
                    create_stage_table(target_cursor, "dbo.service_churn_com_latest", com_stage)
                    com_insert_sql = build_insert_sql(
                        com_stage,
                        com_insert_columns,
                    ).replace(
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        "VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
                    )
                    com_fetch_wait_started = time.perf_counter()
                    normalized_com_rows = com_fetch_future.result()
                    com_total_rows = len(normalized_com_rows)
                    com_fetch_seconds = log_phase_duration("commercial churn fetch wait", com_fetch_wait_started, com_total_rows)
                    com_insert_started = time.perf_counter()
                    append_rows_with_mode(
                        target_cursor,
                        com_insert_sql,
                        normalized_com_rows,
                        use_fast_executemany=False,
                        chunk_size=COM_CHURN_INSERT_CHUNK_SIZE,
                        commit_connection=target,
                        commit_every_chunks=CHURN_STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                        progress_label="commercial churn",
                        progress_log_every=CHURN_PROGRESS_LOG_EVERY,
                        batch_timing_label="commercial churn insert",
                    )
                    com_insert_seconds = log_phase_duration("commercial churn stage insert", com_insert_started, com_total_rows)
                    com_replace_started = time.perf_counter()
                    com_sync_counts = sync_table_slice_from_stage(
                        target,
                        target_cursor,
                        "dbo.service_churn_com_latest",
                        com_stage,
                        ["PredictionMonth"],
                        com_sync_key_columns,
                        com_sync_compare_columns,
                    )
                    com_replace_seconds = log_phase_duration("commercial churn target replace", com_replace_started, com_total_rows)
                else:
                    com_batch_details_started = time.perf_counter()
                    com_batch_details = get_churn_batch_details(com_source_query)
                    com_fetch_seconds = log_phase_duration("commercial churn batch discovery", com_batch_details_started, len(com_batch_details))
                    com_insert_started = time.perf_counter()
                    com_stage_handles = []
                    try:
                        com_stage_handles, com_total_rows = load_parallel_churn_stage_tables(
                            "dbo.service_churn_com_latest",
                            com_insert_columns,
                            com_source_query,
                            com_batch_details,
                            "commercial churn",
                            CHURN_STAGE_WORKERS,
                        )
                        com_insert_seconds = log_phase_duration("commercial churn parallel stage load", com_insert_started, com_total_rows)
                        com_replace_started = time.perf_counter()
                        com_sync_counts = sync_table_slice_from_stage_tables(
                            target,
                            target_cursor,
                            "dbo.service_churn_com_latest",
                            [stage_table for stage_table, _, _ in com_stage_handles],
                            ["PredictionMonth"],
                            com_sync_key_columns,
                            com_sync_compare_columns,
                        )
                        com_replace_seconds = log_phase_duration("commercial churn target replace", com_replace_started, com_total_rows)
                    finally:
                        cleanup_parallel_stage_tables(com_stage_handles)
                target.commit()
                log_churn_sync_summary(
                    "dbo.service_churn_com_latest",
                    com_sync_counts["deleted_rows"],
                    com_sync_counts["updated_rows"],
                    com_sync_counts["inserted_rows"],
                )
                log_churn_refresh_summary(
                    "dbo.service_churn_com_latest",
                    com_source_mode,
                    com_total_rows,
                    com_fetch_seconds,
                    com_insert_seconds,
                    com_replace_seconds,
                )
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_com_latest")
                log_table_refresh("dbo.service_churn_com_latest", com_total_rows)
                mark_table_completed(checkpoint_state, "dbo.service_churn_com_latest")
                if com_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_com_latest", com_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if call_monthly_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_call_monthly_agg")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_call_monthly_agg")
                call_monthly_insert_columns = [
                    "AccountNumber", "CustomerType", "MonthStart", "ContactMonthStart", "NumberOfCalls",
                    "AverageAgentTalkMin", "AverageTotalContactDurationMin", "TotalAgentTalkMin", "TotalContactDurationMin", "RefreshedAt",
                ]
                call_monthly_sync_key_columns = ["AccountNumber", "CustomerType", "MonthStart"]
                call_monthly_sync_compare_columns = [
                    "ContactMonthStart",
                    "NumberOfCalls",
                    "AverageAgentTalkMin",
                    "AverageTotalContactDurationMin",
                    "TotalAgentTalkMin",
                    "TotalContactDurationMin",
                ]
                call_monthly_fetch_wait_started = time.perf_counter()
                normalized_call_monthly_rows = call_monthly_fetch_future.result()
                log_phase_duration("call monthly aggregate fetch wait", call_monthly_fetch_wait_started, len(normalized_call_monthly_rows))
                call_monthly_insert_started = time.perf_counter()
                call_monthly_stage_handles = []
                call_monthly_stage_tables = []
                call_monthly_total_rows = len(normalized_call_monthly_rows)
                try:
                    try:
                        call_monthly_stage_handles, call_monthly_total_rows = load_parallel_rows_to_stage_tables(
                            "dbo.service_churn_call_monthly_agg",
                            call_monthly_insert_columns,
                            normalized_call_monthly_rows,
                            "call monthly aggregate",
                            CALL_TABLE_STAGE_WORKERS,
                            CALL_TABLE_INSERT_CHUNK_SIZE,
                            STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                            CALL_TABLE_PROGRESS_LOG_EVERY,
                        )
                        call_monthly_stage_tables = [stage_table for stage_table, _, _ in call_monthly_stage_handles]
                    except pyodbc.Error as exc:
                        cleanup_parallel_stage_tables(call_monthly_stage_handles)
                        call_monthly_stage_handles = []
                        logger.warning(
                            "Parallel stage load failed for call monthly aggregate: %s; falling back to single-stage insert",
                            exc,
                        )
                        call_monthly_stage = make_stage_table_name("dbo.service_churn_call_monthly_agg")
                        create_stage_table(target_cursor, "dbo.service_churn_call_monthly_agg", call_monthly_stage)
                        call_monthly_insert_sql = build_insert_sql(
                            call_monthly_stage,
                            call_monthly_insert_columns,
                        ).replace(
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
                        )
                        append_rows_with_mode(
                            target_cursor,
                            call_monthly_insert_sql,
                            normalized_call_monthly_rows,
                            use_fast_executemany=False,
                            chunk_size=CALL_TABLE_INSERT_CHUNK_SIZE,
                            commit_connection=target,
                            commit_every_chunks=STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                            progress_label="call monthly aggregate",
                            progress_log_every=CALL_TABLE_PROGRESS_LOG_EVERY,
                            batch_timing_label="call monthly aggregate insert",
                        )
                        call_monthly_stage_tables = [call_monthly_stage]
                    log_phase_duration("call monthly aggregate stage insert", call_monthly_insert_started, len(normalized_call_monthly_rows))
                    call_monthly_replace_started = time.perf_counter()
                    call_monthly_sync_counts = sync_table_slice_from_stage_tables(
                        target,
                        target_cursor,
                        "dbo.service_churn_call_monthly_agg",
                        call_monthly_stage_tables,
                        ["MonthStart"],
                        call_monthly_sync_key_columns,
                        call_monthly_sync_compare_columns,
                    )
                finally:
                    cleanup_parallel_stage_tables(call_monthly_stage_handles)
                log_phase_duration("call monthly aggregate target replace", call_monthly_replace_started, len(normalized_call_monthly_rows))
                log_table_sync_summary(
                    "dbo.service_churn_call_monthly_agg",
                    call_monthly_sync_counts["deleted_rows"],
                    call_monthly_sync_counts["updated_rows"],
                    call_monthly_sync_counts["inserted_rows"],
                )
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_call_monthly_agg")
                log_table_refresh("dbo.service_churn_call_monthly_agg", len(normalized_call_monthly_rows))
                mark_table_completed(checkpoint_state, "dbo.service_churn_call_monthly_agg")
                if call_monthly_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_call_monthly_agg", call_monthly_source_fingerprint)
                target, target_cursor = reconnect_target(target)

            if call_records_skipped:
                logger.info("Skipping already refreshed table=%s", "dbo.service_churn_call_records_monthly")
            else:
                logger.info("Refreshing table=%s", "dbo.service_churn_call_records_monthly")
                call_records_stage = make_stage_table_name("dbo.service_churn_call_records_monthly")
                create_stage_table(target_cursor, "dbo.service_churn_call_records_monthly", call_records_stage)
                call_records_sync_key_columns = ["CustomerAccount", "SubscriberAccount", "CustomerType", "MonthStart"]
                call_records_sync_compare_columns = ["NumberOfCalls", "TotalDurationMinutes", "AvgDurationMinutes", "ClientSentiment", "IsResolved"]
                call_records_insert_sql = build_insert_sql(
                    call_records_stage,
                    ["CustomerAccount", "SubscriberAccount", "CustomerType", "MonthStart", "NumberOfCalls", "TotalDurationMinutes", "AvgDurationMinutes", "ClientSentiment", "IsResolved", "RefreshedAt"],
                ).replace(
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME())",
                )
                call_records_fetch_wait_started = time.perf_counter()
                normalized_call_records_rows = call_records_fetch_future.result()
                log_phase_duration("call records monthly fetch wait", call_records_fetch_wait_started, len(normalized_call_records_rows))
                call_records_insert_started = time.perf_counter()
                append_rows_with_mode(
                    target_cursor,
                    call_records_insert_sql,
                    normalized_call_records_rows,
                    use_fast_executemany=False,
                    chunk_size=CALL_TABLE_INSERT_CHUNK_SIZE,
                    commit_connection=target,
                    commit_every_chunks=STAGE_INSERT_COMMIT_EVERY_CHUNKS,
                    progress_label="call records monthly",
                    progress_log_every=CALL_TABLE_PROGRESS_LOG_EVERY,
                )
                log_phase_duration("call records monthly stage insert", call_records_insert_started, len(normalized_call_records_rows))
                call_records_replace_started = time.perf_counter()
                call_records_sync_counts = sync_table_slice_from_stage(
                    target,
                    target_cursor,
                    "dbo.service_churn_call_records_monthly",
                    call_records_stage,
                    ["MonthStart"],
                    call_records_sync_key_columns,
                    call_records_sync_compare_columns,
                )
                log_phase_duration("call records monthly target replace", call_records_replace_started, len(normalized_call_records_rows))
                log_table_sync_summary(
                    "dbo.service_churn_call_records_monthly",
                    call_records_sync_counts["deleted_rows"],
                    call_records_sync_counts["updated_rows"],
                    call_records_sync_counts["inserted_rows"],
                )
                stamp_table_refreshed_at(target, target_cursor, "dbo.service_churn_call_records_monthly")
                log_table_refresh("dbo.service_churn_call_records_monthly", len(normalized_call_records_rows))
                mark_table_completed(checkpoint_state, "dbo.service_churn_call_records_monthly")
                if call_records_source_fingerprint is not None:
                    mark_source_fingerprint(source_state, "dbo.service_churn_call_records_monthly", call_records_source_fingerprint)

        target.commit()
        reset_checkpoint_state()
        log_phase_duration("total refresh", refresh_started)
        write_refresh_audit("SUCCESS", "Refresh completed")
        logger.info("Refresh completed successfully.")
    except Exception as exc:
        safe_rollback(target)
        log_phase_duration("total refresh before failure", refresh_started)
        write_refresh_audit("FAILED", str(exc))
        raise
    finally:
        if source_sql is not None:
            source_sql.close()
        target.close()


if __name__ == "__main__":
    refresh_once()