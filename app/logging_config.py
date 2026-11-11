from __future__ import annotations

import json
import logging
import sys

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                   "logger": record.name, "event": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class RedactingFilter(logging.Filter):
    """Masks configured secret values anywhere in the rendered log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        for key, value in list(record.__dict__.items()):
            if isinstance(value, str) and key not in _STANDARD:
                for s in self.secrets:
                    value = value.replace(s, "***")
                record.__dict__[key] = value
        return True


def configure_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(secrets or []))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry credentials
