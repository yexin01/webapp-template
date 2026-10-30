"""ASGI entrypoint (local server and Vercel Python runtime).

On Vercel this file is the `api` service of the root vercel.json (Vercel Services,
Beta). It is verified locally by `pnpm test:vercel-build`; a deployment is not.
"""

from app.bootstrap import bootstrap, create_app

app = create_app(bootstrap())
