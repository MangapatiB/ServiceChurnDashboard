import csv
from datetime import datetime
from io import StringIO

from flask import Blueprint, current_app, jsonify, render_template, request, Response

from app.services.data_service import DashboardDataService
from app.services.query_builders import normalize_customer_segment, normalize_limit


dashboard_bp = Blueprint("dashboard", __name__)


def _as_excel_text(value: str | int | None) -> str:
    value_str = str(value or "").strip()
    if not value_str:
        return ""
    return f'="{value_str}"'


def _as_exact_number(value) -> str:
    if value in (None, ""):
        return ""
    try:
        numeric = float(value)
        return f"{numeric:.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _csv_response(filename_prefix: str, fieldnames: list[str], rows: list[dict[str, str]]) -> Response:
    output = StringIO()
    output.write("\ufeff")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}_{timestamp}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _get_dashboard_service() -> DashboardDataService:
    service = current_app.extensions.get("dashboard_data_service")
    if service is None:
        service = DashboardDataService(current_app.config)
        current_app.extensions["dashboard_data_service"] = service
    return service


@dashboard_bp.get("/healthz")
def health_check():
    return jsonify({"status": "ok"}), 200


@dashboard_bp.get("/")
def dashboard_home():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    customer_page = normalize_limit(request.args.get("customer_page"), default=1, minimum=1, maximum=100000)
    customer_page_size = normalize_limit(request.args.get("customer_page_size"), default=15, minimum=1, maximum=250)
    customer_sort = "asc" if str(request.args.get("customer_sort", "desc")).strip().lower() == "asc" else "desc"
    with service.open_query_session() as query_session:
        snapshot = service.get_dashboard_snapshot(
            location=location,
            limit=limit,
            customer_segment=customer_segment,
            customer_page=customer_page,
            customer_page_size=customer_page_size,
            customer_sort=customer_sort,
            query_session=query_session,
        )
        location_options = service.get_location_options(query_session=query_session)
    return render_template(
        "index.html",
        initial_snapshot=snapshot,
        location_options=location_options,
        refresh_seconds=current_app.config["REFRESH_SECONDS"],
        data_mode=current_app.config["DATA_SOURCE_MODE"],
        current_location=location,
        current_limit=limit,
        current_segment=customer_segment,
        current_customer_page=snapshot.get("meta", {}).get("customer_page", customer_page),
        current_customer_page_size=snapshot.get("meta", {}).get("customer_page_size", customer_page_size),
        current_customer_sort=snapshot.get("meta", {}).get("customer_sort", customer_sort),
        max_dashboard_limit=current_app.config["MAX_DASHBOARD_LIMIT"],
    )


@dashboard_bp.get("/operations")
def operations_view():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    with service.open_query_session() as query_session:
        snapshot = service.get_dashboard_snapshot(
            location=location,
            limit=limit,
            customer_segment=customer_segment,
            query_session=query_session,
        )
        location_options = service.get_location_options(query_session=query_session)
    return render_template(
        "operations.html",
        initial_snapshot=snapshot,
        location_options=location_options,
        refresh_seconds=current_app.config["REFRESH_SECONDS"],
        data_mode=current_app.config["DATA_SOURCE_MODE"],
        current_location=location,
        current_limit=limit,
        current_segment=customer_segment,
        max_dashboard_limit=current_app.config["MAX_DASHBOARD_LIMIT"],
    )


@dashboard_bp.get("/call-data")
def call_data_view():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    page = normalize_limit(request.args.get("page"), default=1, minimum=1, maximum=100000)
    page_size = normalize_limit(
        request.args.get("page_size"),
        default=100,
        minimum=1,
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    with service.open_query_session() as query_session:
        call_data = service.get_call_data_records(
            location=location,
            limit=limit,
            customer_segment=customer_segment,
            page=page,
            page_size=page_size,
            query_session=query_session,
        )
        location_options = service.get_location_options(query_session=query_session)
    return render_template(
        "call_data.html",
        initial_snapshot=None,
        call_data=call_data,
        location_options=location_options,
        refresh_seconds=current_app.config["REFRESH_SECONDS"],
        data_mode=current_app.config["DATA_SOURCE_MODE"],
        current_location=location,
        current_limit=limit,
        current_page=call_data.get("meta", {}).get("page", page),
        current_page_size=call_data.get("meta", {}).get("page_size", page_size),
        current_segment=customer_segment,
        max_dashboard_limit=current_app.config["MAX_DASHBOARD_LIMIT"],
    )


@dashboard_bp.get("/api/dashboard")
def dashboard_api():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    customer_page = normalize_limit(request.args.get("customer_page"), default=1, minimum=1, maximum=100000)
    customer_page_size = normalize_limit(request.args.get("customer_page_size"), default=15, minimum=1, maximum=250)
    customer_sort = "asc" if str(request.args.get("customer_sort", "desc")).strip().lower() == "asc" else "desc"
    return jsonify(
        service.get_dashboard_snapshot(
            location=location,
            limit=limit,
            customer_segment=customer_segment,
            customer_page=customer_page,
            customer_page_size=customer_page_size,
            customer_sort=customer_sort,
        )
    )


@dashboard_bp.get("/api/dashboard/customers")
def dashboard_customers_api():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    customer_page = normalize_limit(request.args.get("customer_page"), default=1, minimum=1, maximum=100000)
    customer_page_size = normalize_limit(request.args.get("customer_page_size"), default=15, minimum=1, maximum=250)
    customer_sort = "asc" if str(request.args.get("customer_sort", "desc")).strip().lower() == "asc" else "desc"
    return jsonify(
        service.get_dashboard_customer_page(
            location=location,
            limit=limit,
            customer_segment=customer_segment,
            customer_page=customer_page,
            customer_page_size=customer_page_size,
            customer_sort=customer_sort,
        )
    )


@dashboard_bp.get("/api/dashboard/export")
def dashboard_export_api():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    customer_sort = "asc" if str(request.args.get("customer_sort", "desc")).strip().lower() == "asc" else "desc"
    
    # Get all customers (no pagination)
    customer_data = service.get_all_filtered_customers(
        location=location,
        limit=limit,
        customer_segment=customer_segment,
        customer_sort=customer_sort,
    )
    
    fieldnames = [
        "Customer ID",
        "Geo",
        "Phone",
        "Churn Probability",
        "Drivers",
        "Last Event",
        "Next Action",
        "Modem Model",
        "Modem Health Score",
    ]
    rows = []
    for customer in customer_data.get("customers", []):
        rows.append(
            {
                "Customer ID": _as_excel_text(customer.get("customer_id", "")),
                "Geo": customer.get("geo", ""),
                "Phone": _as_excel_text(customer.get("phone_number", "")),
                "Churn Probability": _as_exact_number(customer.get("churn_probability", "")),
                "Drivers": customer.get("drivers", ""),
                "Last Event": customer.get("last_event", ""),
                "Next Action": customer.get("next_action", ""),
                "Modem Model": customer.get("modem_model", ""),
                "Modem Health Score": _as_exact_number(customer.get("modem_health_score", "")),
            }
        )
    return _csv_response("churn_dashboard_export", fieldnames, rows)


@dashboard_bp.get("/api/operations/export")
def operations_export_api():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    snapshot = service.get_dashboard_snapshot(
        location=location,
        limit=limit,
        customer_segment=customer_segment,
    )
    fieldnames = [
        "Geo",
        "Flagged Accounts",
        "Avg Risk",
        "90+ Risk",
        "Contactable",
        "Top Driver",
        "Tier",
        "Recommended Action",
    ]
    rows = []
    for item in snapshot.get("geo_summary", []):
        rows.append(
            {
                "Geo": item.get("geo", ""),
                "Flagged Accounts": _as_exact_number(item.get("flagged_accounts", "")),
                "Avg Risk": _as_exact_number(item.get("avg_risk", "")),
                "90+ Risk": _as_exact_number(item.get("high_risk_count", "")),
                "Contactable": _as_exact_number(item.get("contactable_count", "")),
                "Top Driver": item.get("top_driver", ""),
                "Tier": item.get("risk_tier", ""),
                "Recommended Action": item.get("recommended_action", ""),
            }
        )
    return _csv_response("operations_watchlist_export", fieldnames, rows)


@dashboard_bp.get("/api/call-data/export")
def call_data_export_api():
    service = _get_dashboard_service()
    location = request.args.get("location", "")
    limit = normalize_limit(
        request.args.get("limit"),
        default=current_app.config["HIGH_RISK_LIMIT"],
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    customer_segment = normalize_customer_segment(request.args.get("segment"))
    page = normalize_limit(request.args.get("page"), default=1, minimum=1, maximum=100000)
    page_size = normalize_limit(
        request.args.get("page_size"),
        default=100,
        minimum=1,
        maximum=current_app.config["MAX_DASHBOARD_LIMIT"],
    )
    call_data = service.get_call_data_records(
        location=location,
        limit=limit,
        customer_segment=customer_segment,
        page=page,
        page_size=page_size,
    )
    fieldnames = [
        "Customer Account",
        "Subscriber Account",
        "Customer Type",
        "Month",
        "Number Of Calls",
        "Total Duration (Min)",
        "Average Duration (Min)",
        "Client Sentiment",
        "Resolved",
    ]
    rows = []
    for item in call_data.get("rows", []):
        rows.append(
            {
                "Customer Account": _as_excel_text(item.get("customer_account", "")),
                "Subscriber Account": _as_excel_text(item.get("subscriber_account", "")),
                "Customer Type": item.get("customer_type", ""),
                "Month": item.get("month_start", "") or "-",
                "Number Of Calls": _as_exact_number(item.get("number_of_calls", "")),
                "Total Duration (Min)": _as_exact_number(item.get("total_duration_minutes", "")),
                "Average Duration (Min)": _as_exact_number(item.get("avg_duration_minutes", "")),
                "Client Sentiment": item.get("client_sentiment", ""),
                "Resolved": "Yes" if item.get("is_resolved") else "No",
            }
        )
    return _csv_response("call_data_export", fieldnames, rows)
