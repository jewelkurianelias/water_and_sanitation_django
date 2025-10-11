from django.test import TestCase
from unittest.mock import patch

from main.services import animal_cards_service as svc


class AnimalCardsServiceTests(TestCase):
    """Unit tests for animal_cards_service (kids_cards_service)."""

    def test_fetch_kids_cards_local_returns_list(self):
        """Test that fetch_kids_cards() returns a non-empty list when using local data."""
        with patch.object(svc, "_use_local_data", return_value=True):
            data = svc.fetch_kids_cards()
            self.assertIsInstance(data, list)
            self.assertTrue(len(data) > 0)
            # Check that each item has expected keys
            for item in data:
                self.assertIn("card_order", item)
                self.assertIn("title", item)
                self.assertIn("lead_html", item)
                self.assertIn("detail_html", item)
                self.assertIn("hint_text", item)
                self.assertIn("read_seconds", item)
                self.assertIn("is_starter", item)

    def test_fetch_collect_cards_local_returns_list(self):
        """Test that fetch_collect_cards() returns a non-empty list with correct keys."""
        with patch.object(svc, "_use_local_data", return_value=True):
            items = svc.fetch_collect_cards()
            self.assertIsInstance(items, list)
            self.assertTrue(len(items) > 0)
            for item in items:
                self.assertIn("no", item)
                self.assertIn("title", item)
                self.assertIn("aria", item)
                self.assertIn("img", item)
                self.assertIn("desc", item)
                self.assertIn("special", item)
                self.assertIn("levelCap", item)
                self.assertIn("levelCur", item)

    def test_build_collect_cards_json_returns_json_string(self):
        """Test that build_collect_cards_json() returns a JSON string."""
        with patch.object(svc, "_use_local_data", return_value=True):
            json_str = svc.build_collect_cards_json(limit=3)
            self.assertIsInstance(json_str, str)
            import json
            data = json.loads(json_str)
            self.assertIsInstance(data, list)
            self.assertTrue(len(data) <= 3)
