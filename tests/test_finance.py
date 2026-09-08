import unittest
from finance_domain import money, transaction, budget, recurring, budget_usage


class FinanceTests(unittest.TestCase):
    def test_reject_invalid_money(self):
        for value in [0, -1, 'NaN', 'Infinity', '0.001', None, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)
        self.assertEqual(money('1234.56'), 1234.56)

    def test_edit_keeps_valid_date_and_transfer(self):
        row = transaction(dict(type='ย้ายเงิน', amount=100, date='2026-08-31', account='เงินสด', category='ShopeeWallet', note='เติมเงิน'))
        self.assertEqual(row['date'], '31/08/2026')
        self.assertEqual(row['account'], 'เงินสด')
        self.assertEqual(row['category'], 'ShopeeWallet')
        with self.assertRaises(ValueError):
            transaction({**row, 'date':'2026-02-30'})
        with self.assertRaises(ValueError):
            transaction({**row, 'date':'2026-08-31', 'category':'เงินสด'})

    def test_unpaid_bill_allows_no_account_paid_requires_account(self):
        row = dict(type='รายจ่ายต้องชำระต่อเดือน', amount=100, date='2026-09-01', account='-', category='Internet', status='ยังไม่จ่าย')
        self.assertEqual(transaction(row)['status'], 'ยังไม่จ่าย')
        with self.assertRaises(ValueError): transaction({**row,'status':'จ่ายแล้ว'})

    def test_budget_scoped_to_month_excludes_loans_and_unpaid_bills(self):
        plans=[{'id':'b','month':'2026-09-01','category':'อาหาร','amount':100}]
        rows=[dict(date='8/9/2026',category='อาหาร',type='รายจ่าย',amount=80),
              dict(date='9/9/2026',category='อาหาร',type='รายจ่ายต้องชำระต่อเดือน',amount=15,status='จ่ายแล้ว'),
              dict(date='9/9/2026',category='อาหาร',type='รายจ่ายต้องชำระต่อเดือน',amount=100,status='ยังไม่จ่าย'),
              dict(date='8/8/2026',category='อาหาร',type='รายจ่าย',amount=100),
              dict(date='8/9/2026',category='อาหาร',type='ให้ยืมเงิน',amount=100)]
        result=budget_usage(plans, rows)[0]
        self.assertEqual(result['spent'],95)
        self.assertEqual(result['remaining'],5)
        self.assertEqual(result['percent'],95)

    def test_budget_and_recurrence_validation(self):
        self.assertEqual(budget(dict(month='2026-09',category='อาหาร',amount=3000))['month'],'2026-09-01')
        with self.assertRaises(ValueError): budget(dict(month='2026-13',category='อาหาร',amount=3000))
        base=dict(type='รายรับ',category='เงินเดือน',account='กสิกร',amount=30000,day_of_month=31,start_date='2026-09-08')
        self.assertEqual(recurring(base)['day_of_month'],31)
        with self.assertRaises(ValueError): recurring({**base,'day_of_month':32})


if __name__ == '__main__': unittest.main()
