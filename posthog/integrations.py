# Compatibility shim for posthog.integrations
from typing import Any
from posthog.models.integration import Integration, IntegrationManager


class IntegrationNotFound(Exception):
    pass


class IntegrationRecord:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


__all__ = ["Integration", "IntegrationManager", "IntegrationNotFound", "IntegrationRecord"]
