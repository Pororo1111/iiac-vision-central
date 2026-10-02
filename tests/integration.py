"""로컬 개발 서버와 실제 PostgreSQL을 검증하고 임시 장치·이벤트를 제거한다."""
import concurrent.futures
from datetime import datetime, timezone, timedelta
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import unittest
from urllib import request, error, parse
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from simulate_iot import config
from local_fixtures import DeviceFixture
ENV = config()
BASE = os.environ.get('CENTRAL_TEST_URL', 'http://localhost:' + ENV.get('CENTRAL_PORT', '8200'))

class Session:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookies))
    def call(self, method, path, body=None, headers=None, form=False):
        headers = dict(headers or {})
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
            body = (parse.urlencode(body) if form else json.dumps(body)).encode()
        req = request.Request(BASE + '/api' + path, data=body, method=method, headers=headers)
        try:
            response = self.opener.open(req, timeout=10)
        except error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            try: result = json.loads(data) if data else None
            except ValueError: result = {'nonJson': True}
            return response.status, result
    def csrf(self):
        status, token = self.call('GET', '/auth/csrf')
        assert status == 200
        return {token['headerName']: token['token']}
    def login(self):
        return self.call('POST', '/auth/login', {'username': ENV['ADMIN_USERNAME'], 'password': ENV['ADMIN_PASSWORD']}, self.csrf(), True)

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = DeviceFixture()
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.admin = Session()
        assert cls.admin.login()[0] == 200, '관리자 로그인 실패'
        cls.iot = Session()
        cls.headers = {'Authorization': 'Bearer ' + cls.fixture.token}
        cls.device = cls.fixture.id
        assert cls.iot.call('POST', '/iot/register', {'deviceId': cls.device, 'name':'통신 검증 장치'}, cls.headers)[0] == 200
    def event(self, **extra):
        return {'eventId': str(uuid.uuid4()), 'deviceId': self.device, 'cameraId':'test-camera', 'zoneId':'test-zone', 'type':'INTRUSION_START', 'occurredAt':datetime.now(timezone.utc).isoformat(), 'objectCount':1, **extra}
    def test_01_auth_csrf_and_logout(self):
        s = Session()
        self.assertEqual(s.call('GET','/devices')[0],401)
        self.assertEqual(s.call('POST','/auth/login',{'username':ENV['ADMIN_USERNAME'],'password':ENV['ADMIN_PASSWORD']},form=True)[0],403)
        self.assertEqual(s.call('POST','/auth/login',{'username':ENV['ADMIN_USERNAME'],'password':'incorrect'},s.csrf(),True)[0],401)
        before = next(c.value for c in s.cookies if c.name == 'JSESSIONID')
        self.assertEqual(s.login()[0],200)
        after = next(c for c in s.cookies if c.name == 'JSESSIONID')
        self.assertNotEqual(before,after.value)
        self.assertIn('HttpOnly',after._rest)
        self.assertEqual(s.call('GET','/auth/me')[1]['username'], ENV['ADMIN_USERNAME'])
        self.assertEqual(s.call('POST','/auth/logout')[0],403)
        self.assertEqual(s.call('POST','/auth/logout',headers=s.csrf())[0],204)
        self.assertEqual(s.call('GET','/devices')[0],401)
    def test_02_device_auth_isolation(self):
        body={'deviceId':self.device,'modelName':'simulator / no camera','runtimeStatus':'RUNNING'}
        self.assertEqual(self.admin.call('POST','/iot/heartbeat',body)[0],401)
        self.assertEqual(self.iot.call('POST','/iot/heartbeat',body,{'Authorization':'Bearer wrong'})[0],401)
        self.assertEqual(self.iot.call('POST','/iot/heartbeat',{**body,'deviceId':'another-device'},self.headers)[0],403)
        self.assertEqual(self.iot.call('POST','/iot/register',{'deviceId':'unprovisioned','name':'x'},self.headers)[0],403)
        self.assertEqual(self.iot.call('GET','/devices',headers=self.headers)[0],401)
        self.assertEqual(self.iot.call('POST','/iot/heartbeat',body,self.headers)[0],200)
        self.assertEqual(self.iot.call('POST','/iot/heartbeat',{**body,'runtimeStatus':'UNKNOWN'},self.headers)[0],200)
        devices=self.admin.call('GET','/devices')[1]
        device=next(d for d in devices if d['deviceId']==self.device)
        self.assertTrue(device['online'])
        self.assertEqual(device['runtimeStatus'],'UNKNOWN')
        self.assertFalse(any('token' in k.lower() for k in device))
    def test_03_duplicate_concurrency(self):
        body=self.event()
        def send(_): return Session().call('POST','/iot/events',body,self.headers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(send,range(4)))
        self.assertTrue(all(s==200 for s,b in results))
        self.assertEqual(sum(not b['duplicate'] for s,b in results),1)
        events=self.admin.call('GET','/events?size=100')[1]['items']
        self.assertEqual(sum(e['eventId']==body['eventId'] for e in events),1)
    def test_04_validation_and_search(self):
        for override in [{'eventId':'bad-uuid'}, {'objectCount':-1}, {'type':'OTHER'}, {'cameraId':''}, {'occurredAt':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}]:
            self.assertEqual(self.iot.call('POST','/iot/events',self.event(**override),self.headers)[0],400)
        self.assertEqual(self.iot.call('POST','/iot/events',self.event(deviceId='another-device'),self.headers)[0],403)
        self.assertEqual(self.admin.call('GET','/events?size=101')[0],400)
        self.assertEqual(self.admin.call('GET','/events?page=-1')[0],400)
        self.assertEqual(self.admin.call('GET','/events?type=OTHER')[0],400)
        self.assertEqual(self.admin.call('GET','/events?from=bad-date')[0],400)
        self.assertEqual(self.admin.call('GET','/events?from=2026-02-02T00:00:00Z&to=2026-01-01T00:00:00Z')[0],400)
        start=datetime.now(timezone.utc).isoformat()
        for _ in range(3):
            self.assertEqual(self.iot.call('POST','/iot/events',self.event(type='INTRUSION_END',objectCount=0),self.headers)[0],200)
        query=parse.urlencode({'deviceId':self.device,'type':'INTRUSION_END','from':start,'size':2})
        status,data=self.admin.call('GET','/events?'+query)
        self.assertEqual(status,200,data)
        self.assertEqual(data['total'],3)
        self.assertEqual(len(data['items']),2)
        next_page=self.admin.call('GET','/events?'+query+'&page=1')[1]
        self.assertEqual(len(next_page['items']),1)
        self.assertFalse({e['eventId'] for e in data['items']} & {e['eventId'] for e in next_page['items']})
        self.assertEqual(self.admin.call('GET','/events?'+parse.urlencode({'deviceId':"' OR 1=1 --"}))[1]['total'],0)

if __name__ == '__main__': unittest.main(verbosity=2)
