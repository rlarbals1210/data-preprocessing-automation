"""웹 흐름 오류 — 화면·API가 `{code, message, details, location}`으로 보여 준다(TD 12-1)."""

from __future__ import annotations


class FlowError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details
