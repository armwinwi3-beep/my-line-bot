"""Linking a repayment to a loan, with an in-memory fake of the Supabase client."""
import unittest
from types import SimpleNamespace

from debts import check_repay_link


class Query:
    def __init__(self, rows):
        self.rows, self.filters = rows, []

    def select(self, *_): return self
    def limit(self, *_): return self
    def eq(self, key, value): self.filters.append((key, value)); return self

    def execute(self):
        return SimpleNamespace(data=[r for r in self.rows if all(r.get(k) == v for k, v in self.filters)])


class DB:
    def __init__(self, rows): self.rows = rows
    def table(self, name): assert name == "transactions"; return Query(self.rows)


def rows():
    return [
        {"id": 1, "user_id": "U1", "type": "ให้ยืมเงิน", "category": "Dd", "amount": 200},
        {"id": 2, "user_id": "U1", "type": "ได้คืนจากลูกหนี้", "category": "Dd", "amount": 50, "repays_id": 1},
        {"id": 3, "user_id": "U1", "type": "ให้ยืมเงิน", "category": "ต้น", "amount": 100},
        {"id": 4, "user_id": "U2", "type": "ให้ยืมเงิน", "category": "Dd", "amount": 999},
        {"id": 5, "user_id": "U1", "type": "รายจ่าย", "category": "Dd", "amount": 10},
    ]


class CheckRepayLinkTest(unittest.TestCase):
    def test_accepts_up_to_remaining(self):
        self.assertEqual(check_repay_link(DB(rows()), "U1", "1", "Dd", "150"), 1)

    def test_rejects_more_than_remaining(self):
        with self.assertRaisesRegex(ValueError, "150.00"):
            check_repay_link(DB(rows()), "U1", 1, "Dd", 151)

    def test_rejects_other_users_loan(self):
        with self.assertRaisesRegex(ValueError, "ไม่พบ"):
            check_repay_link(DB(rows()), "U1", 4, "Dd", 10)

    def test_rejects_other_persons_loan(self):
        with self.assertRaisesRegex(ValueError, "คนอื่น"):
            check_repay_link(DB(rows()), "U1", 3, "Dd", 10)

    def test_rejects_non_loan_rows(self):
        for bad in (2, 5):
            with self.assertRaisesRegex(ValueError, "ไม่พบ"):
                check_repay_link(DB(rows()), "U1", bad, "Dd", 1)

    def test_rejects_bad_id_and_amount(self):
        with self.assertRaisesRegex(ValueError, "ไม่ถูกต้อง"):
            check_repay_link(DB(rows()), "U1", "abc", "Dd", 1)
        with self.assertRaisesRegex(ValueError, "มากกว่า 0"):
            check_repay_link(DB(rows()), "U1", 1, "Dd", 0)


if __name__ == "__main__":
    unittest.main()
