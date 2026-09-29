"""도메인 예외. 서비스 계층은 HTTP를 모르고 이 예외만 던지며, main.py의 핸들러가 HTTP 응답으로 바꾼다.

응답 형태: {"detail": "사람이 읽는 설명", "code": "기계가 분기할 코드"}
"""


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class InvalidState(AppError):
    """상태 전이 규칙 위반 (예: 학습 중인 모델 삭제, done 아닌 모델로 예측)."""
    status_code = 409
    code = "invalid_state"


class InvalidInput(AppError):
    status_code = 422
    code = "invalid_input"


class InsufficientData(AppError):
    status_code = 422
    code = "insufficient_data"


class LookaheadViolation(AppError):
    """시점 규칙 위반: 그 시점엔 알 수 없었던(미래) 데이터를 쓰려는 시도."""
    status_code = 422
    code = "lookahead_violation"


class SourceError(AppError):
    """외부 데이터 소스 호출 실패 (타임아웃, 5xx, 이상한 응답)."""
    status_code = 502
    code = "source_error"
