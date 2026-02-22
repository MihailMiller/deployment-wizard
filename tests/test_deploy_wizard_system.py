import unittest

from deploy_wizard.system import _merged_dns


class DeployWizardSystemTests(unittest.TestCase):
    def test_merged_dns_uses_fallback_when_missing(self) -> None:
        self.assertEqual(_merged_dns(None), ["1.1.1.1", "8.8.8.8"])

    def test_merged_dns_filters_loopback_values(self) -> None:
        self.assertEqual(_merged_dns(["127.0.0.53", "::1"]), ["1.1.1.1", "8.8.8.8"])

    def test_merged_dns_preserves_existing_non_loopback_entries(self) -> None:
        self.assertEqual(
            _merged_dns(["10.0.0.2"]),
            ["10.0.0.2", "1.1.1.1", "8.8.8.8"],
        )

    def test_merged_dns_deduplicates_existing_fallback_entries(self) -> None:
        self.assertEqual(
            _merged_dns(["8.8.8.8", "1.1.1.1", "8.8.8.8"]),
            ["8.8.8.8", "1.1.1.1"],
        )


if __name__ == "__main__":
    unittest.main()
