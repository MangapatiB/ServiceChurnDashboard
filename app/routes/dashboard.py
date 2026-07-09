from flask import Blueprint, current_app, jsonify, render_template, request

from app.services.data_service import DashboardDataService
from app.services.query_builders import normalize_customer_segment, normalize_limit


dashboard_bp = Blueprint("dashboard", __name__)


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
