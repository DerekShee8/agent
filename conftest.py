"""Repository-root pytest wiring.

The NiceGUI user plugin must be loaded from a top-level conftest: pytest rejects
``pytest_plugins`` in a non-root conftest, so ``tests/conftest.py`` is not an
option. ``user_plugin`` is used rather than ``plugin`` because the latter pulls
in selenium-backed screen fixtures that are not installed here.
"""

pytest_plugins = ["nicegui.testing.user_plugin"]
