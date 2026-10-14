"""Shared kernel: the platform every feature module builds on.

The kernel holds what is common to all modules: tenancy and authorization
primitives, errors, pagination, domain events, jobs, the diagnostics probe
contract, and the database, observability, and HTTP infrastructure. It never
imports a feature module (``app.modules``); an import-linter contract enforces
this.
"""
