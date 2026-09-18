from __future__ import annotations

import unittest
from scripts.frontend_api_inventory import inventory as api_inventory
from tests import frontend_map


class FrontendPhase0GovernanceTest(unittest.TestCase):
    def test_frontend_locations_have_one_canonical_map(self) -> None:
        self.assertTrue(frontend_map.FRONTEND_SOURCE_DIR.is_dir())
        self.assertEqual(frontend_map.FRONTEND_BUILD_INDEX.name, "index.html")
        self.assertEqual(frontend_map.FRONTEND_ASSETS_DIR.name, "assets")

    def test_route_inventory_is_unique_and_frozen_for_the_migration(self) -> None:
        routes = api_inventory()
        identities = {(route.method, route.path) for route in routes}
        self.assertEqual(len(routes), 72)
        self.assertEqual(len(identities), len(routes))
        self.assertIn(("GET", "/"), identities)
        self.assertIn(("POST", "/chat"), identities)
        self.assertIn(("GET", "/workout_state"), identities)

if __name__ == "__main__":
    unittest.main()
