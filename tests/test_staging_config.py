import unittest

from app.config import Settings


class StagingConfigTests(unittest.TestCase):
    def test_staging_mode_is_disabled_by_default(self):
        self.assertFalse(Settings(_env_file=None).staging_mode)

    def test_staging_mode_accepts_explicit_environment_alias(self):
        settings = Settings(_env_file=None, SAS_STAGING_MODE="true")
        self.assertTrue(settings.staging_mode)


if __name__ == "__main__":
    unittest.main()
