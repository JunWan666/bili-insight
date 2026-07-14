from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AnalysisErrorCode(StrEnum):
    DEPENDENCY_UNAVAILABLE = "ANALYSIS_DEPENDENCY_UNAVAILABLE"
    INVALID_MEDIA = "ANALYSIS_INVALID_MEDIA"
    INVALID_CONFIGURATION = "ANALYSIS_INVALID_CONFIGURATION"
    PROCESS_FAILED = "ANALYSIS_PROCESS_FAILED"
    PROCESS_TIMEOUT = "ANALYSIS_PROCESS_TIMEOUT"
    PROCESS_OUTPUT_LIMIT = "ANALYSIS_PROCESS_OUTPUT_LIMIT"
    PROCESS_RESOURCE_LIMIT = "ANALYSIS_PROCESS_RESOURCE_LIMIT"
    CANCELED = "ANALYSIS_CANCELED"
    MODEL_FAILED = "ANALYSIS_MODEL_FAILED"
    EXPORT_FAILED = "ANALYSIS_EXPORT_FAILED"


@dataclass(frozen=True, slots=True)
class AnalysisFailure:
    code: AnalysisErrorCode
    message: str
    action: str
    diagnostic: str | None = None


class AnalysisError(RuntimeError):
    """Safe, user-actionable error raised by local analysis services."""

    def __init__(self, failure: AnalysisFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def invalid_configuration(message: str) -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.INVALID_CONFIGURATION,
            message=message,
            action="检查分析参数后重新提交",
        )
    )
