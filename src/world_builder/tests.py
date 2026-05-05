# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for world-builder.

These tests verify the package is importable and the test runner is wired
correctly. They will be augmented with real synthetic-fixture coverage as
implementation begins.
"""
from django.test import TestCase

import world_builder


class SmokeTest(TestCase):
    """End-to-end install + runner sanity check."""

    def test_package_importable(self):
        self.assertTrue(hasattr(world_builder, "__version__"))

    def test_version_is_string(self):
        self.assertIsInstance(world_builder.__version__, str)
