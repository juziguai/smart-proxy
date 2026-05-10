import unittest

from pricing import estimate_usage_cost


class PricingTests(unittest.TestCase):
    def test_estimates_deepseek_flash_api_cost(self):
        cost = estimate_usage_cost(
            "deepseek-v4-flash",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 2_000_000,
                "cache_read_input_tokens": 3_000_000,
                "cache_creation_input_tokens": 4_000_000,
            },
        )

        self.assertTrue(cost["billable"])
        self.assertEqual(cost["billing_type"], "api")
        self.assertEqual(cost["currency"], "CNY")
        self.assertAlmostEqual(cost["input"], 1.0)
        self.assertAlmostEqual(cost["output"], 4.0)
        self.assertAlmostEqual(cost["cache_read"], 0.06)
        self.assertAlmostEqual(cost["cache_write"], 4.0)
        self.assertAlmostEqual(cost["total"], 9.06)

    def test_marks_token_plan_models_as_non_billable(self):
        cost = estimate_usage_cost(
            "MiniMax-M2.7-highspeed",
            {
                "input_tokens": 1_000_000,
                "output_tokens": 2_000_000,
                "cache_read_input_tokens": 3_000_000,
                "cache_creation_input_tokens": 4_000_000,
            },
        )

        self.assertFalse(cost["billable"])
        self.assertEqual(cost["billing_type"], "token_plan")
        self.assertEqual(cost["total"], 0)


if __name__ == "__main__":
    unittest.main()
