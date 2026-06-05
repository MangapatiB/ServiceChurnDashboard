import re


def sanitize_location(value: str) -> str:
    normalized = (value or "").strip().upper()
    normalized = re.sub(r"[^A-Z0-9\s\-']", "", normalized)
    return normalized.replace("'", "''")


def normalize_customer_segment(value: str | None) -> str:
    normalized = (value or "res").strip().lower()
    return "com" if normalized == "com" else "res"


def normalize_limit(value: int | str | None, default: int = 25, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sanitize_account_numbers(account_numbers: list[str]) -> list[str]:
    sanitized_accounts = []
    seen_accounts = set()
    for account in account_numbers:
        normalized_account = str(account).strip() if account is not None else ""
        if not normalized_account or not normalized_account.isdigit() or normalized_account in seen_accounts:
            continue
        seen_accounts.add(normalized_account)
        sanitized_accounts.append(normalized_account)
    return sanitized_accounts


def build_truckroll_query(location: str, limit: int) -> str:
    safe_location = sanitize_location(location)
    safe_limit = normalize_limit(limit)
    location_clause = f"AND upper(TRSR.BillingCity) = '{safe_location}'" if safe_location else ""

    return f"""
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
    TRSR.accountnumber AS LegacyAccountNumber,
    TRSR.SubscriberAccountNumber,
    PULSE.PhoneNumber,
        upper(TRSR.BillingCity) AS BillingCity
FROM TRSR
LEFT JOIN HSD ON HSD.SubscriberAccountNumber = TRSR.SubscriberAccountNumber
INNER JOIN prod.bronze.pulsedb_optin AS PULSE
    ON PULSE.AccountNumber = TRSR.accountnumber
WHERE TRSR.prediction = 1
    {location_clause}
  AND LOWER(PULSE.OptOutStatus) = 'false'
  AND HSD.HSD = 1
    AND TRSR.SubscriberAccountNumber IS NOT NULL
LIMIT {safe_limit}
""".strip()


def build_location_options_query() -> str:
    return """
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
    SELECT TR.*, SR.SubscriberAccountNumber, SR.BillingCity
    FROM TR
    LEFT JOIN SR ON TR.accountnumber = SR.LegacyAccountId
    WHERE TR.rank = 1
)
SELECT DISTINCT upper(TRSR.BillingCity) AS BillingCity
FROM TRSR
LEFT JOIN HSD ON HSD.SubscriberAccountNumber = TRSR.SubscriberAccountNumber
INNER JOIN prod.bronze.pulsedb_optin AS PULSE
    ON PULSE.AccountNumber = TRSR.accountnumber
WHERE TRSR.prediction = 1
  AND LOWER(PULSE.OptOutStatus) = 'false'
  AND HSD.HSD = 1
  AND TRSR.SubscriberAccountNumber IS NOT NULL
  AND TRSR.BillingCity IS NOT NULL
ORDER BY BillingCity
""".strip()


def build_account_mac_mapping_query() -> str:
        return """
SELECT accountnumber, cmac
FROM prod.featurestore.cmdata_15day
WHERE accountnumber IS NOT NULL
    AND cmac IS NOT NULL
""".strip()


def build_account_mac_mapping_subset_query(account_numbers: list[str]) -> str:
        sanitized_accounts = _sanitize_account_numbers(account_numbers)
        if not sanitized_accounts:
                return ""

        in_clause = ", ".join(sanitized_accounts)
        return f"""
SELECT CAST(accountnumber AS STRING) AS accountnumber, cmac
FROM prod.featurestore.cmdata_15day
WHERE accountnumber IS NOT NULL
    AND cmac IS NOT NULL
    AND CAST(accountnumber AS BIGINT) IN ({in_clause})
""".strip()


def build_churn_query(account_numbers: list[str], customer_segment: str = "res") -> str:
    sanitized_accounts = _sanitize_account_numbers(account_numbers)
    if not sanitized_accounts:
        return ""

    safe_segment = normalize_customer_segment(customer_segment)
    churn_table = "prod.featurestore.com_shap_category_v2" if safe_segment == "com" else "prod.featurestore.res_shap_category_v2"
    in_clause = ", ".join(sanitized_accounts)

    return f"""
SELECT
    CAST(SubscriberAccountNumber AS STRING) AS SubscriberAccountNumber,
    churn_probability * 100 AS churn_probability,
    prediction_month,
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
    END AS top1_feature,
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
    END AS top2_feature,
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
    END AS top3_feature
FROM {churn_table}
WHERE prediction_month = (
    SELECT MAX(prediction_month)
        FROM {churn_table}
)
  AND CAST(SubscriberAccountNumber AS BIGINT) IN ({in_clause})
ORDER BY churn_probability DESC
""".strip()


def build_call_data_query(
    account_numbers: list[str] | None = None,
    location: str = "",
    customer_segment: str = "res",
) -> str:
    sanitized_accounts = _sanitize_account_numbers(account_numbers or [])
    safe_segment = normalize_customer_segment(customer_segment)
    customer_type_code = "COM" if safe_segment == "com" else "RES"
    account_filter_clause = ""
    if sanitized_accounts:
        account_filter_clause = (
            "AND ("
            "CAST(ctn.SUB_ACCT_NO_CTN AS STRING) IN "
            f"({', '.join(quote_sql_string(account) for account in sanitized_accounts)}) "
            "OR CAST(ctn.CUST_ACCT_NO_CTN AS STRING) IN "
            f"({', '.join(quote_sql_string(account) for account in sanitized_accounts)})"
            ")"
        )

    return f"""
WITH Phone_call AS (
    SELECT
        COUNT(*) AS NumberOfCalls,
        CAST(ctn.SUB_ACCT_NO_CTN AS STRING) AS AccountNumber,
        DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS MonthStart,
        DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS ContactMonthStart,
        ROUND(AVG(
            (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
        ), 2) AS AverageAgentTalkmin,
        ROUND(AVG(
            (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
        ), 2) AS AverageTotalContactDurationmin,
        ROUND(SUM(
            (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
        ), 2) AS TotalAgentTalkmin,
        ROUND(SUM(
            (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
        ), 2) AS TotalContactDurationmin
    FROM prod.bronze.dnadatawarehouse_ctn_interaction ctn
    INNER JOIN prod.bronze.dnadatawarehouse_sbb_base sbb
        ON ctn.CUST_ACCT_NO_CTN = sbb.CUST_ACCT_NO_SBB
    WHERE ctn.INTR_TYP_CTN IN ('Call In', 'Outbound Call')
      AND ctn.START_DTE_CTN >= DATE_ADD(CURRENT_DATE(), -365)
      AND ctn.SUB_ACCT_NO_CTN IS NOT NULL
      AND UPPER(TRIM(sbb.CUST_TYP_SBB)) = '{customer_type_code}'
      {account_filter_clause}
    GROUP BY
        CAST(ctn.SUB_ACCT_NO_CTN AS STRING),
        DATE_TRUNC('month', ctn.START_DTE_TME_CTN)
)
SELECT * FROM Phone_call
""".strip()


def build_call_data_records_query(account_numbers: list[str] | None = None, customer_segment: str = "res") -> str:
    sanitized_accounts = _sanitize_account_numbers(account_numbers or [])
    if not sanitized_accounts:
        return ""

    safe_segment = normalize_customer_segment(customer_segment)
    customer_type_code = "COM" if safe_segment == "com" else "RES"

    account_filter_clause = (
        "AND ("
        "CAST(ctn.SUB_ACCT_NO_CTN AS STRING) IN "
        f"({', '.join(quote_sql_string(account) for account in sanitized_accounts)}) "
        "OR CAST(ctn.CUST_ACCT_NO_CTN AS STRING) IN "
        f"({', '.join(quote_sql_string(account) for account in sanitized_accounts)})"
        ")"
    )

    return f"""
SELECT
    CAST(ctn.CUST_ACCT_NO_CTN AS STRING) AS CustomerAccount,
    CAST(ctn.SUB_ACCT_NO_CTN AS STRING) AS SubscriberAccount,
    sbb.CUST_TYP_SBB AS CustomerType,
    DATE_TRUNC('month', ctn.START_DTE_TME_CTN) AS MonthStart,
    COUNT(*) AS NumberOfCalls,
    ROUND(SUM(
        (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
    ), 2) AS TotalDurationMinutes,
    ROUND(AVG(
        (unix_timestamp(ctn.FINISH_DTE_TME_CTN) - unix_timestamp(ctn.START_DTE_TME_CTN)) / 60.0
    ), 2) AS AvgDurationMinutes
FROM prod.bronze.dnadatawarehouse_ctn_interaction ctn
INNER JOIN prod.bronze.dnadatawarehouse_sbb_base sbb
    ON ctn.CUST_ACCT_NO_CTN = sbb.CUST_ACCT_NO_SBB
WHERE ctn.INTR_TYP_CTN IN ('Call In', 'Outbound Call')
  AND ctn.START_DTE_CTN >= DATE_ADD(CURRENT_DATE(), -365)
  AND ctn.SUB_ACCT_NO_CTN IS NOT NULL
    AND UPPER(TRIM(sbb.CUST_TYP_SBB)) = '{customer_type_code}'
  {account_filter_clause}
GROUP BY
    CAST(ctn.CUST_ACCT_NO_CTN AS STRING),
    CAST(ctn.SUB_ACCT_NO_CTN AS STRING),
    sbb.CUST_TYP_SBB,
    DATE_TRUNC('month', ctn.START_DTE_TME_CTN)
ORDER BY MonthStart DESC, NumberOfCalls DESC
""".strip()
