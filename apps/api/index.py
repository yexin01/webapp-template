"""ASGI entrypoint (local server and Vercel Python runtime).

Vercel compatibility is not yet verified; see DEC-001 in
dev-docs/engineering-implementation-plan.md.
"""

from app.composition import bootstrap
from app.http.app import create_app

app = create_app(bootstrap())
