"""Auth rules for every /api route, without calling LINE or Supabase."""
import ast
import os
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask, jsonify, request

import auth


def make_app():
    app = Flask(__name__)

    @app.errorhandler(PermissionError)
    def unauthorized(error):
        return jsonify(message=str(error)), 401

    @app.get("/who")
    def who():
        return jsonify(user=auth.verify_user(request, request.args.get("user_id")))

    return app


def line_profile(user_id, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = {"userId": user_id}
    return mock.patch.object(auth.requests, "get", return_value=response)


class VerifyUserTest(unittest.TestCase):
    def setUp(self):
        auth._failures.clear()
        auth._token_cache.clear()
        self.env = mock.patch.dict(os.environ, {"ADMIN_PIN": "1234"})
        self.env.start()
        self.client = make_app().test_client()

    def tearDown(self):
        self.env.stop()

    def get(self, user_id, headers=None, ip="1.1.1.1"):
        query = "" if user_id is None else f"?user_id={user_id}"
        return self.client.get(f"/who{query}", headers=headers or {}, environ_base={"REMOTE_ADDR": ip})

    def test_no_credentials_is_rejected(self):
        self.assertEqual(self.get("admin").status_code, 401)
        self.assertEqual(self.get("Uabc").status_code, 401)

    def test_missing_user_id_is_rejected_even_with_pin(self):
        self.assertEqual(self.get(None, {"X-Admin-Pin": "1234"}).status_code, 401)

    def test_admin_pin_can_act_as_admin_and_line_users(self):
        for user in ("admin", "Uabc"):
            res = self.get(user, {"X-Admin-Pin": "1234"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json["user"], user)

    def test_wrong_pin_is_rejected(self):
        self.assertEqual(self.get("admin", {"X-Admin-Pin": "0000"}).status_code, 401)

    def test_unset_admin_pin_never_matches(self):
        with mock.patch.dict(os.environ, {"ADMIN_PIN": ""}):
            self.assertEqual(self.get("admin", {"X-Admin-Pin": ""}).status_code, 401)
            self.assertEqual(self.get("admin", {"X-Admin-Pin": "x"}).status_code, 401)

    def test_pin_lockout_after_repeated_failures(self):
        for _ in range(auth.MAX_PIN_FAILURES):
            self.get("admin", {"X-Admin-Pin": "0000"})
        locked = self.get("admin", {"X-Admin-Pin": "1234"})
        self.assertEqual(locked.status_code, 401)
        self.assertIn("15", locked.json["message"])
        # another client is unaffected
        self.assertEqual(self.get("admin", {"X-Admin-Pin": "1234"}, ip="2.2.2.2").status_code, 200)

    def test_line_token_must_match_user(self):
        with line_profile("Uabc"):
            self.assertEqual(self.get("Uabc", {"Authorization": "Bearer t"}).status_code, 200)
            self.assertEqual(self.get("Uother", {"Authorization": "Bearer t"}).status_code, 401)

    def test_verified_token_is_cached(self):
        with line_profile("Uabc") as get:
            for _ in range(3):
                self.assertEqual(self.get("Uabc", {"Authorization": "Bearer t"}).status_code, 200)
            self.assertEqual(get.call_count, 1)
            # cached token still cannot act as a different user
            self.assertEqual(self.get("Uother", {"Authorization": "Bearer t"}).status_code, 401)

    def test_rejected_token_is_not_cached(self):
        with line_profile(None, status=401) as get:
            self.get("Uabc", {"Authorization": "Bearer bad"})
            self.get("Uabc", {"Authorization": "Bearer bad"})
            self.assertEqual(get.call_count, 2)

    def test_cached_token_expires(self):
        with line_profile("Uabc") as get, mock.patch.object(auth.time, "monotonic", return_value=1000.0):
            self.get("Uabc", {"Authorization": "Bearer t"})
        with line_profile("Uabc") as get, mock.patch.object(auth.time, "monotonic", return_value=1000.0 + auth.TOKEN_CACHE_SECONDS + 1):
            self.get("Uabc", {"Authorization": "Bearer t"})
            self.assertEqual(get.call_count, 1)

    def test_line_token_cannot_act_as_admin(self):
        with line_profile("admin"):
            self.assertEqual(self.get("admin", {"Authorization": "Bearer t"}).status_code, 401)

    def test_invalid_line_token_is_rejected(self):
        with line_profile(None, status=401):
            self.assertEqual(self.get("Uabc", {"Authorization": "Bearer bad"}).status_code, 401)

    def test_line_outage_is_rejected_not_crashing(self):
        with mock.patch.object(auth.requests, "get", side_effect=auth.requests.ConnectionError()):
            self.assertEqual(self.get("Uabc", {"Authorization": "Bearer t"}).status_code, 401)


class RoutesUseVerifyUserTest(unittest.TestCase):
    def test_every_api_route_calls_verify_user(self):
        tree = ast.parse((Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8"))
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            routes = [d for d in node.decorator_list if isinstance(d, ast.Call)
                      and getattr(d.func, "attr", "") == "route"
                      and isinstance(d.args[0], ast.Constant) and d.args[0].value.startswith("/api/")]
            if not routes:
                continue
            checked += 1
            calls = {getattr(n.func, "id", None) for n in ast.walk(node) if isinstance(n, ast.Call)}
            self.assertIn("verify_user", calls, node.name)
            source = ast.unparse(node)
            self.assertNotIn("'admin')", source, f"{node.name} still defaults to admin")
        self.assertEqual(checked, 5)


if __name__ == "__main__":
    unittest.main()
