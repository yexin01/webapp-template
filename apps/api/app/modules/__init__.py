"""Feature modules. Every module has the same layout (ADR-0003):

- ``domain``: pure rules, permissions, and events. No frameworks.
- ``ports``: the module's repository and external-service protocols.
- ``service``: use cases. Depends on ports, the domain, and the kernel.
- ``adapters``: implementations of the ports (SQL, SDKs, fakes).
- ``probe``: optional integration probe for the diagnostics page.
- ``routes``: FastAPI router factories and request/response models.
- ``api``: optional public surface for other modules.
- ``settings``: optional environment variables and their validation.
- ``module``: wiring. Builds the module from the kernel and settings.

Modules depend on the kernel. A module may use another module only through
that module's ``api``. Import-linter and ``tests/architecture`` enforce these
rules. ``pnpm new:module <name>`` scaffolds a new module.
"""
