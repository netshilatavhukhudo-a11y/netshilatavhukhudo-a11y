"""Tool implementations.

Every file here defines exactly one capability. `nexus.registry` imports the
modules named in `enabled_tools`, which is what runs their `@tool` decorators
and populates `nexus.tools.base.REGISTRY`.
"""
