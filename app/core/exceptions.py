from __future__ import annotations


class PipelineError(Exception):
    """Base class for all application errors."""


class ConfigError(PipelineError):
    """Invalid or missing configuration."""


class IntegrationError(PipelineError):
    """An external service returned a non-retryable error."""


class TransientError(IntegrationError):
    """Timeouts, HTTP 429/5xx and similar failures that are safe to retry."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class WorkerValidationError(PipelineError):
    """A worker received a payload that violates its input contract."""


class WorkerExecutionError(PipelineError):
    """Raised by ``BaseWorker.execute`` for ANY failure; wraps the original exception."""

    def __init__(self, worker: str, cause: BaseException, duration_s: float = 0.0):
        super().__init__(f"{worker} failed: {type(cause).__name__}: {cause}")
        self.worker = worker
        self.cause = cause
        self.duration_s = duration_s
        self.transient = isinstance(cause, TransientError)


class ExtractionError(PipelineError):
    """LLM output could not be validated within the retry budget."""


class ModelArtifactError(PipelineError):
    """Model artifact missing, untrusted or incompatible with the feature schema."""
