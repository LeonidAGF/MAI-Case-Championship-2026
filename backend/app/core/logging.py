from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "method", "route", "status", "duration_ms", "scenario", "method_used", "affected",
            "dataset", "export_format", "requested_region", "organisation_id", "key_id", "export_rows", "export_bytes",
            "partner_operation", "search_results", "territory_id", "settlement_name", "settlement_region", "settlement_request",
        ):
            if hasattr(record, key):
                fields[key] = getattr(record, key)
        if record.exc_info:
            fields["exception"] = self.formatException(record.exc_info)
        return json.dumps(fields, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(getattr(h, "_atlas", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter()
    handler.setFormatter(formatter)
    handler._atlas = True
    root.addHandler(handler)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / f"api-{os.getpid()}.jsonl", maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler._atlas = True
        root.addHandler(file_handler)
    for name in ("httpx", "httpx2", "asyncio"):
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
