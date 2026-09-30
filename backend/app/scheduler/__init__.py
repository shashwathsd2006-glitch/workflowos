"""Persistent scheduler and background worker (Phase 8).

The scheduler decides *when* an automation should run; the worker decides
*how* it runs. Both keep their state in SQLite and both delegate the actual
workflow execution to the existing engine in ``app.automation.engine``.
"""
