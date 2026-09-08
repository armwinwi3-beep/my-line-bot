import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from finance_api import register_finance


class Query:
    def __init__(self, store, table):
        self.store=store; self.table_name=table; self.filters=[]; self.action='read'; self.values=None; self.slice=None
    def select(self,*args): return self
    def eq(self,key,value): self.filters.append((key,value)); return self
    def order(self,*args): return self
    def range(self,start,end): self.slice=(start,end); return self
    def update(self,values): self.action='update'; self.values=values; return self
    def upsert(self,values,**kwargs): self.action='upsert'; self.values=values; return self
    def execute(self):
        rows=self.store.setdefault(self.table_name,[])
        selected=[r for r in rows if all(r.get(k)==v for k,v in self.filters)]
        if self.action=='update':
            for row in selected: row.update(self.values)
        if self.action=='upsert':
            if not any(row.get('id')==self.values.get('id') for row in rows): rows.append(self.values.copy())
        if self.slice: selected=selected[self.slice[0]:self.slice[1]+1]
        return SimpleNamespace(data=selected)


class DB:
    def __init__(self): self.store={'transactions':[dict(id=1,user_id='U1',date='08/09/2026',type='รายจ่าย',amount=50,category='อาหาร',account='เงินสด'),dict(id=2,user_id='U2',date='08/09/2026',type='รายจ่าย',amount=99,category='อาหาร',account='เงินสด')]}
    def table(self,name): return Query(self.store,name)
    def rpc(self,*args): return SimpleNamespace(execute=lambda:SimpleNamespace(data=0))


class APITests(unittest.TestCase):
    def setUp(self):
        self.db=DB(); app=Flask(__name__)
        with patch.dict(os.environ,{'SUPABASE_SERVICE_ROLE_KEY':'test-only','ADMIN_PIN':'test-pin'}):
            register_finance(app,self.db,lambda *_:self.db,'https://example.invalid')
        self.client=app.test_client()
        self.profile=patch('finance_api.requests.get',return_value=SimpleNamespace(status_code=200,json=lambda:{'userId':'U1'}))
        self.profile.start(); self.addCleanup(self.profile.stop)
        self.headers={'Authorization':'Bearer fake-token'}

    def test_history_requires_verified_identity_and_filters_other_users(self):
        self.assertEqual(self.client.get('/api/finance/records?user_id=U1').status_code,401)
        self.assertEqual(self.client.get('/api/finance/records?user_id=U2',headers=self.headers).status_code,401)
        response=self.client.get('/api/finance/records?user_id=U1',headers=self.headers)
        self.assertEqual([r['id'] for r in response.json['records']],[1])

    def test_cannot_edit_another_users_record(self):
        data=dict(user_id='U1',id=2,type='รายจ่าย',amount=100,date='2026-09-08',account='เงินสด',category='อาหาร')
        self.assertEqual(self.client.post('/api/finance/record',json=data,headers=self.headers).status_code,404)
        self.assertEqual(self.db.store['transactions'][1]['amount'],99)

    def test_edit_changes_date_account_amount_without_changing_owner_or_id(self):
        data=dict(user_id='U1',id=1,type='รายจ่าย',amount=100,date='2026-08-31',account='ShopeeWallet',category='อาหาร')
        response=self.client.post('/api/finance/record',json=data,headers=self.headers)
        self.assertEqual(response.status_code,200)
        row=self.db.store['transactions'][0]
        self.assertEqual((row['id'],row['user_id'],row['date'],row['amount'],row['account']),(1,'U1','31/08/2026',100,'ShopeeWallet'))

    def test_admin_requires_server_pin(self):
        with patch.dict(os.environ,{'ADMIN_PIN':'test-pin'}):
            self.assertEqual(self.client.get('/api/finance/records?user_id=admin').status_code,401)
            self.assertEqual(self.client.get('/api/finance/records?user_id=admin',headers={'X-Admin-Pin':'test-pin'}).status_code,200)

    def test_retrying_same_recurring_request_does_not_create_two_rules(self):
        data=dict(user_id='U1',id='00000000-0000-4000-8000-000000000001',type='รายรับ',amount=100,date='2099-01-01',start_date='2099-01-01',day_of_month=31,account='เงินสด',category='เงินเดือน')
        for _ in range(2): self.assertEqual(self.client.post('/api/finance/recurring',json=data,headers=self.headers).status_code,200)
        self.assertEqual(len(self.db.store['recurring_rules']),1)


if __name__=='__main__': unittest.main()
