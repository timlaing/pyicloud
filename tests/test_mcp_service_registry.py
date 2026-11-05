"""Fixed provider registration never loads caller-selected modules or accounts."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pyicloud.mcp.services import register_services


def test_registry_loads_only_selected_installed_providers() -> None:
    """Missing and unselected services cannot expand the server's tool surface."""
    provider = SimpleNamespace(register=MagicMock())
    context = SimpleNamespace(config=SimpleNamespace(services=("calendar", "notes")))
    with (
        patch(
            "pyicloud.mcp.services.find_spec", side_effect=[object(), None]
        ) as discover,
        patch("pyicloud.mcp.services.import_module", return_value=provider) as load,
    ):
        register_services("server", context, "reads", "additional", "content")
    assert discover.call_count == 2
    load.assert_called_once_with("pyicloud.mcp.services.calendar")
    provider.register.assert_called_once_with(
        "server", context, "reads", "additional", "content"
    )
