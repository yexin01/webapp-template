"""Email has no HTTP routes: other modules send through ``api.py``, and its check runs from the diagnostics routes.

The router exists so that every module has the same layers (the import-linter ``layers`` contract). A Resend
webhook (bounces and complaints, signed with Svix like Clerk's) would be mounted here.
"""

from fastapi import APIRouter


def build_router() -> APIRouter:
    return APIRouter(tags=["email"])
