"""Exercise the add route's account-scoped slip idempotency without live writes."""
import ast
import re
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.pattern = None
        self.values = None

    def select(self, *_): return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def ilike(self, key, value): self.pattern = (key, value); return self
    def limit(self, *_): return self
    def insert(self, values): self.values = values; return self

    def execute(self):
        if self.values is not None:
            values = self.values if isinstance(self.values, list) else [self.values]
            inserted = []
            for value in values:
                if value.get('slip_reference') and any(
                    row['user_id'] == value['user_id'] and row.get('slip_reference') == value['slip_reference']
                    for row in self.rows
                ):
                    raise ValueError('duplicate slip reference')
                row = dict(value, id=len(self.rows) + 1)
                self.rows.append(row)
                inserted.append(row)
            return SimpleNamespace(data=inserted)
        found = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters)]
        if self.pattern:
            key, pattern = self.pattern
            found = [row for row in found if str(row.get(key, '')).lower().endswith(pattern.lstrip('%').lower())]
        return SimpleNamespace(data=found[:1])


class DB:
    def __init__(self): self.rows = []
    def table(self, name):
        assert name == 'transactions'
        return Query(self.rows)


def route(db):
    tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'api_add')
    function.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    fake_request = SimpleNamespace(json=None)
    fake_request.get_json = lambda silent=False: fake_request.json
    # auth is covered by test_auth.py; here every caller is trusted as the user it names
    scope = dict(request=fake_request, jsonify=lambda value: value, supabase=db, re=re, Decimal=Decimal,
                 verify_user=lambda _request, user_id: user_id,
                 InvalidOperation=InvalidOperation, datetime=datetime, TH_TZ=timezone(timedelta(hours=7)))
    exec(compile(module, 'app.py', 'exec'), scope)
    def post(payload):
        fake_request.json = payload
        result = scope['api_add']()
        return result if isinstance(result, tuple) else (result, 200)
    return post


class SlipDedupTests(unittest.TestCase):
    def test_same_user_retries_do_not_insert_but_another_user_can(self):
        db = DB()
        post = route(db)
        payload = dict(user_id='admin', type='transfer', sourceAccount='กรุงไทย',
                       destinationAccount='กสิกร', amount='1.00', fee='0',
                       slipReference='a67e96b380dc2475d', note='จากสลิป · อ้างอิง A67E96B380DC2475D')
        first, _ = post(payload)
        retry, _ = post(payload)
        other, _ = post(dict(payload, user_id='U123'))
        self.assertEqual(first['status'], 'success')
        self.assertFalse(first.get('duplicate', False))
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['id'], first['id'])
        self.assertFalse(other.get('duplicate', False))
        self.assertEqual(len(db.rows), 2)

    def test_bad_reference_is_rejected_without_inserting(self):
        db = DB()
        _, status = route(db)(dict(user_id='admin', type='expense', amount='1', slipReference='0.00บาท'))
        self.assertEqual(status, 400)
        self.assertEqual(db.rows, [])

    def test_older_client_note_is_checked_against_account(self):
        db = DB()
        post = route(db)
        payload = dict(user_id='admin', type='expense', account='กสิกร', category='จ่ายบิล',
                       amount='35.00', note='จากสลิป กสิกร · อ้างอิง TESTREF123')
        first, _ = post(payload)
        retry, _ = post(payload)
        self.assertEqual(first['status'], 'success')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(len(db.rows), 1)


if __name__ == '__main__': unittest.main()
