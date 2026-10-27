import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.standalone_pages import router


class StandaloneAuthPageTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_verification_page_is_isolated_and_served(self):
        response = self.client.get("/standalone/verify-email?token=" + "a" * 32)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("SAS PRO", response.text)
        self.assertIn("/api/standalone/auth/verify-email", response.text)
        self.assertIn('name="robots" content="noindex,nofollow"', response.text)

    def test_password_reset_page_posts_to_reset_endpoint(self):
        response = self.client.get("/standalone/reset-password?token=" + "b" * 32)
        self.assertEqual(response.status_code, 200)
        self.assertIn("autocomplete=\"new-password\"", response.text)
        self.assertIn("/api/standalone/auth/reset-password", response.text)
        self.assertIn("password.value !== confirm.value", response.text)

    def test_page_router_does_not_mount_auth_api(self):
        paths = {route.path for route in router.routes}
        self.assertEqual(paths, {"/standalone/verify-email", "/standalone/reset-password"})


if __name__ == "__main__":
    unittest.main()
