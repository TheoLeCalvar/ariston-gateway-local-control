"""Shared fixtures for the HA-level tests (pytest-homeassistant-custom-component)."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ariston_local in every test."""
    yield
