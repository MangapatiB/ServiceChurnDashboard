# Churn Service Dashboard

A Flask and JavaScript dashboard for surfacing service-friction churn risk signals across geographies and customer accounts.

## What is included

- Sparklight-aligned visual system based on the supplied style guide
- Single-page dashboard with KPI cards, signal mix, market watchlist summary, and customer drill-down
- Mock mode for local development
- Live Databricks SQL API mode with environment-based configuration
- Polling refresh for near-real-time updates

## Project structure

```text
Churn_Service_Dashboard/
  app/
    routes/
    services/
    static/
    templates/
  assets/
  config.py
  requirements.txt
  run.py
```

## Setup

1. Create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env`.
4. Start the app with `python3 run.py`.

## Environment variables

- `DATA_SOURCE_MODE=mock` uses seeded data.
- `DATA_SOURCE_MODE=live` enables the Databricks SQL client.
- `DATABRICKS_HOST` should be your workspace URL.
- `DATABRICKS_WAREHOUSE_ID` should be the SQL warehouse ID used by the Statements API.
- `DATABRICKS_TOKEN` should be a Databricks personal access token or service credential token.
- `DATABRICKS_SQL_QUERY` can be used for a single-line query string.
- `DATABRICKS_SQL_QUERY_FILE` is still supported, but the current live implementation builds the truckroll and churn queries in Python so it can inject the selected location and account list.
- `MODEM_HEALTH_REFRESH_SECONDS` controls the middleware cache TTL for account-to-MAC and modem-health lookups. Set this to `3600` for a 60-minute refresh window.
- `MODEM_SQL_SERVER`, `MODEM_SQL_DATABASE`, `MODEM_SQL_USERNAME`, and `MODEM_SQL_PASSWORD` configure the SQL Server modem-health source.
- `MODEM_SQL_DRIVER` defaults to `ODBC Driver 17 for SQL Server` and must exist on the host for `pyodbc` connections to work.
- `MODEM_SQL_ENCRYPT` and `MODEM_SQL_TRUST_SERVER_CERTIFICATE` let you match the SQL Server TLS requirements. Internal IP-based connections often need `MODEM_SQL_ENCRYPT=no` or a trusted certificate chain.

## Current live query flow

The current live dashboard runs the existing Databricks truckroll and churn queries, then enriches the displayed watchlist with modem health in middleware.

### Modem health join flow

- Databricks mapping query: `select accountnumber, cmac from prod.featurestore.cmdata_15day`
- SQL Server modem query: latest row per `imac` from `newbacondata.dbo.cmdata2011` where `ip <> '0.0.0.0'`
- Middleware cache: both lookups refresh on demand every 60 minutes and are shared across requests through the Flask app singleton

This works with the current Flask setup as a shared TTL cache. If you need a strict background refresh every 60 minutes even when no requests arrive, add a scheduler such as APScheduler, Celery, or an external cron job.

### Truckroll candidate query

Defined in [app/services/query_builders.py](/root/MyProjects/Churn_Service_Dashboard/app/services/query_builders.py). This is based on your truckroll prediction query and returns:

- `SubscriberAccountNumber`
- `PhoneNumber`
- `BillingCity`

### Churn driver query

Also defined in [app/services/query_builders.py](/root/MyProjects/Churn_Service_Dashboard/app/services/query_builders.py). This is based on your churn feature query, but fixed so it returns the latest row per selected account rather than a single global `limit 1` row.

The app merges those result sets in [app/services/data_service.py](/root/MyProjects/Churn_Service_Dashboard/app/services/data_service.py).

Update the query builders if your real table or column names differ.

## Current filters

- `location`: optional billing city filter
- `limit`: maximum number of truckroll-flagged accounts to load
