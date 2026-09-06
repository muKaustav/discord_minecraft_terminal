import unittest

from security import csrf_matches


class CsrfTests(unittest.TestCase):
    def test_accepts_matching_tokens(self):
        self.assertTrue(csrf_matches("expected-token", "expected-token"))

    def test_rejects_missing_or_different_tokens(self):
        self.assertFalse(csrf_matches("expected-token", ""))
        self.assertFalse(csrf_matches("expected-token", "other-token"))


if __name__ == "__main__":
    unittest.main()
