from __future__ import annotations

from fastapi import HTTPException, Request

from app.bootstrap import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def resolve_exchange(container: Container, code: str | None):
    codes = sorted(container.exchanges.exchanges)
    code = code or codes[0]
    if code not in container.exchanges.exchanges:
        raise HTTPException(404, f"unknown exchange '{code}'; configured: {codes}")
    return container.exchanges.get(code)
