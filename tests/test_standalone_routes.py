import unittest

from app.standalone_routes import (
    LoginInput,
    PasswordResetRequest,
    RegisterInput,
    ResetInput,
    router,
)


class StandaloneRouteDefinitionTests(unittest.TestCase):
    def test_expected_auth_routes_are_defined(self):
        paths = {(route.path, method) for route in router.routes for method in (route.methods or set())}
        expected = {
            ("/api/standalone/auth/register", "POST"),
            ("/api/standalone/auth/verify-email", "POST"),
            ("/api/standalone/auth/login", "POST"),
            ("/api/standalone/auth/logout", "POST"),
            ("/api/standalone/auth/me", "GET"),
            ("/api/standalone/auth/request-password-reset", "POST"),
            ("/api/standalone/auth/reset-password", "POST"),
        }
        self.assertTrue(expected.issubset(paths))

    def test_password_lengths_are_validated_by_request_models(self):
        with self.assertRaises(ValueError):
            RegisterInput(email="trader@example.com", password="short")
        with self.assertRaises(ValueError):
            ResetInput(token="t" * 32, new_password="short")
        self.assertEqual(LoginInput(email="TRADER@example.com", password="x").email, "trader@example.com")
        with self.assertRaises(ValueError):
            LoginInput(email="not-an-email", password="x")
        self.assertEqual(PasswordResetRequest(email="Trader@Example.com").email, "trader@example.com")


if __name__ == "__main__":
    unittest.main()
