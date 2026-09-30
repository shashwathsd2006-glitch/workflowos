"""Module 6 - Automation Engine and integration executors (mock first).

Executes *approved* workflow drafts through a controlled action registry.
No external service, shell command, HTTP call or dynamic import is reachable
from this package: only handlers in ``app.automation.actions.ACTION_REGISTRY``
can run.
"""
