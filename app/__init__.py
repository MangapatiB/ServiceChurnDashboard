import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from flask import Flask, g, request

from config import Config
from app.routes.dashboard import dashboard_bp
from app.services.data_service import DashboardDataService


def _configure_logging(app: Flask) -> None:
    log_dir = Path(app.config["LOG_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "dashboard.log"
    log_level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=app.config.get("LOG_BACKUP_COUNT", 14),
        encoding="utf-8",
    )
    handler.setLevel(log_level)
    handler.setFormatter(formatter)

    def ensure_handler(logger: logging.Logger) -> None:
        if any(
            isinstance(existing, TimedRotatingFileHandler) and getattr(existing, "baseFilename", "") == str(log_file)
            for existing in logger.handlers
        ):
            logger.setLevel(log_level)
            return
        logger.addHandler(handler)
        logger.setLevel(log_level)

    ensure_handler(app.logger)
    ensure_handler(logging.getLogger("werkzeug"))
    app.logger.info("Dashboard logging configured at %s", log_file)


def _register_request_logging(app: Flask) -> None:
    @app.before_request
    def log_request_start() -> None:
        g.request_started_at = time.perf_counter()
        app.logger.info(
            "Request started | method=%s path=%s args=%s",
            request.method,
            request.path,
            dict(request.args),
        )

    @app.after_request
    def log_request_end(response):
        duration_ms = 0.0
        started_at = getattr(g, "request_started_at", None)
        if started_at is not None:
            duration_ms = (time.perf_counter() - started_at) * 1000
        app.logger.info(
            "Request completed | method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.teardown_request
    def log_request_exception(exc: Exception | None) -> None:
        if exc is None:
            return
        app.logger.exception(
            "Request failed | method=%s path=%s args=%s",
            request.method,
            request.path,
            dict(request.args),
            exc_info=exc,
        )


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.extensions["dashboard_data_service"] = DashboardDataService(app.config)
    _configure_logging(app)
    _register_request_logging(app)
    app.register_blueprint(dashboard_bp)
    return app
