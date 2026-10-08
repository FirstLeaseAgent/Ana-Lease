import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from app import followup,notifications
from test_documents import FakeConn,FakePool,complete_people,INTAKE

class FollowupConn(FakeConn):
    def fetchone(self):
        if 'SELECT email FROM users' in self.query:return {'email':'aortega@firstlease.com.mx'}
        return None

class FollowupTests(unittest.TestCase):
    def routes(self,owner=lambda request:'user'):
        conn=FollowupConn();app=FastAPI();followup.install(app,FakePool(conn),owner,lambda *args:complete_people())
        return {r.path:r.endpoint for r in app.routes},conn
    def test_denies_non_admin_before_listing_or_detail(self):
        routes,conn=self.routes()
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':'someone@example.test'}):
            for path,args in [('/admin/intakes',(None,)),('/admin/intakes/{intake_id}',(INTAKE,None))]:
                with self.assertRaises(HTTPException) as error:routes[path](*args)
                self.assertEqual(error.exception.status_code,403)
        self.assertTrue(all('SELECT email FROM users' in q for q,_ in conn.calls))
    def test_session_required_and_allowlisted_admin_can_list(self):
        def deny(request):raise HTTPException(401,'Login')
        routes,conn=self.routes(deny)
        with self.assertRaises(HTTPException):routes['/admin/intakes'](None)
        self.assertEqual(conn.calls,[])
        routes,conn=self.routes()
        with patch.dict(os.environ,{'CAPTURE_CONFIG_ADMIN_EMAILS':'aortega@firstlease.com.mx'}):
            self.assertEqual(routes['/admin/intakes'](None)['intakes'],[])
    def test_notifications_record_failure_and_retry_only_claimed_rows(self):
        class Conn(FakeConn):
            def fetchone(self):return {'required_missing':2} if 'RETURNING required_missing' in self.query else None
        conn=Conn()
        with patch.object(notifications,'send_notice',side_effect=RuntimeError('no connection')):
            notifications.deliver(FakePool(conn),INTAKE)
        self.assertEqual(conn.calls[-1][1],('failed',INTAKE))
        conn=FakeConn()
        with patch.object(notifications,'send_notice') as send:
            notifications.deliver(FakePool(conn),INTAKE)
            send.assert_not_called()
    def test_notification_success_marks_sent_without_changing_intake(self):
        class Conn(FakeConn):
            def fetchone(self):return {'required_missing':2} if 'RETURNING required_missing' in self.query else None
        conn=Conn()
        with patch.object(notifications,'send_notice') as send:notifications.deliver(FakePool(conn),INTAKE)
        send.assert_called_once_with(INTAKE,2)
        self.assertEqual(conn.calls[-1][1],('sent',INTAKE))
        self.assertFalse(any('UPDATE intakes' in q for q,_ in conn.calls))
