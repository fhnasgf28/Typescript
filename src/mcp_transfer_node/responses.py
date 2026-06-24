from __future__ import annotations


def success_response(data: dict[str, object]) -> dict[str, object]:
    return {"success": True, "data": data, "error": None}


def error_response(code: str, message: str) -> dict[str, object]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}
