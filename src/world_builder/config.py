# SPDX-License-Identifier: BSD-3-Clause
"""Settings dispatch for world-builder."""
import importlib

from django.conf import settings


DEFAULT_READER = "world_builder.readers.github.GitHubReader"


def get_reader_class():
    """Resolve the WORLDBUILDER_READER setting (dotted path) to a class.

    The setting value is a Python dotted path (e.g.
    ``"world_builder.GitHubReader"`` or
    ``"my_consumer.readers.MyReader"``). Defaults to
    ``world_builder.GitHubReader``.

    Construction is the consumer's responsibility — this function
    returns the class only. Reader kwargs are reader-specific and
    supplied by the consumer at construction time.

    Raises:
        ImportError: if the module path cannot be imported.
        AttributeError: if the named attribute does not exist on the module.
    """
    dotted = getattr(settings, "WORLDBUILDER_READER", DEFAULT_READER)
    module_path, _, attr_name = dotted.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)
