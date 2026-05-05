# SPDX-License-Identifier: BSD-3-Clause
"""Test runner for world-builder.

Runs the library's unit tests against tests/test_settings.py — no gamedir
required. Invoke from the library root:

    python runtests.py
"""
import os
import sys

import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")
    django.setup()

    # Evennia uses lazy attribute exports populated by ``evennia._init()``.
    # Real-runtime entry points (server.py, portal.py, evennia_launcher) call
    # this after ``django.setup()`` runs. The test runner does the same here
    # so library code that depends on Evennia's deferred attributes works
    # under tests.
    import evennia
    evennia._init()

    from django.conf import settings
    from django.test.utils import get_runner

    runner = get_runner(settings)()
    failures = runner.run_tests(["world_builder"])
    sys.exit(bool(failures))
