"""Shim module — re-exports heartbeat API from monitor.heartbeat.

Allows bots to import via:
    from heartbeat import beat   # with PYTHONPATH=/hetznercheck

The actual implementation lives in monitor/heartbeat.py.
"""
from monitor.heartbeat import beat, read_all  # noqa: F401
