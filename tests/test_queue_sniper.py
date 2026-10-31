from pathlib import Path
import contextlib
import importlib.util
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('sniper', str(Path(__file__).resolve().parents[1] / 'queue_sniper.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

def event(i, name='Криптография', allowed=True):
    return dict(id=i, name=name, allowSelfJoin=allowed, eventDateTime='2026-11-01')

class Checks(unittest.TestCase):
    def run_case(self, available, mine=None, args=(), replies=None, credentials=None):
        state = dict(cycles=0, joined=[], requests=[], available=available,
                     mine=mine or [], replies=replies or {})
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self): self.handle_api()
            def do_POST(self): self.handle_api()
            def handle_api(self):
                n = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(n)) if n else None
                state['requests'].append((self.command, self.path, body, self.headers.get('Authorization')))
                code, value = 200, None
                custom = state['replies'].get(self.path)
                if custom:
                    code, value = custom.pop(0) if isinstance(custom, list) else custom
                elif self.path in ('/api/auth/login', '/api/auth/refresh'):
                    value = dict(accessToken='test-access', refreshToken='test-refresh')
                elif self.path.startswith('/api/me/queue-events'):
                    value = state['mine']
                elif self.path.startswith('/api/me/available-queue-events'):
                    state['cycles'] += 1
                    value = available(state['cycles']) if callable(available) else available
                elif self.path.startswith('/api/queues/') and not self.path.endswith('/join'):
                    value = {'subjectId': 2}
                elif self.path.endswith('/join'):
                    state['joined'].append(int(self.path.split('/')[3]))
                else:
                    code, value = 404, {}
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                if code == 429: self.send_header('Retry-After', '0')
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sleeps = []
        def sleep(t):
            sleeps.append(t)
            if len(sleeps) > 6: raise KeyboardInterrupt()
            if t < 0: raise ValueError('sleep length must be non-negative')
        out = io.StringIO()
        argv = ['queue_sniper.py', '--site', f'http://127.0.0.1:{server.server_port}', '--email', 'test@example.invalid', *args]
        try:
            with patch.object(sys, 'argv', argv), patch.dict('os.environ', {'FC_PASSWORD': 'dummy'}), patch.object(s, 'alarm'), patch.object(s.time, 'sleep', sleep), contextlib.redirect_stdout(out):
                try: result = s.main(credentials=credentials)
                except SystemExit as exc: result = ('exit', exc.code)
                except KeyboardInterrupt: result = 'stopped'
                except Exception as exc: result = type(exc).__name__
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        state.update(result=result, log=out.getvalue(), sleeps=sleeps)
        return state

    def test_new_queue_appears(self):
        r = self.run_case(lambda n: [] if n < 3 else [event(1)])
        self.assertEqual((r['result'], r['joined'], r['cycles']), (0, [1], 3))
        self.assertTrue(all(x[3] == 'Bearer test-access' for x in r['requests'] if x[1].startswith('/api/me/')))

    def test_saved_credentials_used_for_login_without_prompt(self):
        with patch('builtins.input', side_effect=AssertionError('Unexpected prompt')), patch.object(s.getpass, 'getpass', side_effect=AssertionError('Unexpected password prompt')):
            r = self.run_case([], args=['--check'], credentials=('saved@example.invalid', 'saved-dummy'))
        self.assertEqual(r['result'], 0)
        login = next(x for x in r['requests'] if x[1] == '/api/auth/login')
        self.assertEqual(login[2], {'email': 'test@example.invalid', 'password': 'saved-dummy'})

    def test_filter_ignores_case_and_other_subject(self):
        r = self.run_case([event(1, 'Математика'), event(2, 'ОСНОВЫ КРИПТОГРАФИИ')], args=['--match', 'криптограф'])
        self.assertEqual(r['joined'], [2])

    def test_date_only_name_is_missed_by_filter(self):
        r = self.run_case([event(1, '01.11.2026')], args=['--match', 'криптограф'])
        self.assertEqual(r['joined'], [])

    def test_no_filter_joins_first_existing_queue(self):
        r = self.run_case([event(9, 'Другой предмет'), event(1, 'Криптография')])
        self.assertEqual(r['joined'], [9])

    def test_existing_membership_and_disabled_self_join(self):
        r = self.run_case([event(1), event(2, allowed=False), event(3)], mine=[event(1)])
        self.assertEqual(r['joined'], [3])

    def test_keep_joins_multiple(self):
        r = self.run_case(lambda n: [event(1), event(2)] if n == 1 else [], args=['--keep'])
        self.assertEqual(r['joined'], [1, 2])

    def test_expired_token_refresh(self):
        r = self.run_case([event(1)], replies={'/api/me/queue-events?includePast=false': [(401, {}), (200, [])]})
        self.assertEqual(r['joined'], [1])
        self.assertTrue(any(x[1] == '/api/auth/refresh' for x in r['requests']))

    def test_server_error_recovers(self):
        r = self.run_case([event(1)], replies={'/api/me/queue-events?includePast=false': [(500, {}), (200, [])]})
        self.assertEqual(r['joined'], [1])

    def test_rate_limit_recovers(self):
        r = self.run_case([event(1)], replies={'/api/me/queue-events?includePast=false': [(429, {}), (200, [])]})
        self.assertEqual(r['joined'], [1])

    def test_already_joined_race(self):
        r = self.run_case([event(1)], replies={'/api/queues/1/join': (409, {'error': 'already_joined'})})
        self.assertEqual(r['result'], 0)
        self.assertIn('Уже записан', r['log'])

    def test_wrong_password_stops(self):
        r = self.run_case([event(1)], replies={'/api/auth/login': (401, {})})
        self.assertEqual((r['result'], r['joined']), (1, []))

    def test_temporary_403_is_retried(self):
        r = self.run_case([event(1)], replies={'/api/queues/1/join': [(403, {'error': 'not_open_yet'}), (200, None)]})
        joins = [x for x in r['requests'] if x[1].endswith('/join')]
        self.assertEqual(len(joins), 2)
        self.assertEqual(r['result'], 0)
        self.assertEqual(r['joined'], [])

    def test_bad_login_response_stops_cleanly(self):
        r = self.run_case([], replies={'/api/auth/login': (200, {})})
        self.assertEqual(r['result'], 1)

    def test_negative_interval_rejected(self):
        r = self.run_case([], args=['--interval', '-1'])
        self.assertEqual(r['result'], ('exit', 2))

    def test_subject_id_accepts_date_name(self):
        r = self.run_case([event(1, '01.11.2026')], args=['--subject-id', '2', '--match', 'криптограф'])
        self.assertEqual(r['joined'], [1])

    def test_or_accepts_name_even_with_other_subject_id(self):
        r = self.run_case([event(1)], args=['--subject-id', '99', '--match', 'криптограф'])
        self.assertEqual(r['joined'], [1])

    def test_subject_only_does_not_match_empty_text(self):
        r = self.run_case([event(1)], args=['--subject-id', '99'])
        self.assertEqual(r['joined'], [])

    def test_other_subject_and_name_rejected(self):
        r = self.run_case([event(1, 'Математика')], args=['--subject-id', '99', '--match', 'криптограф'])
        self.assertEqual(r['joined'], [])

    def test_check_never_joins_even_matching_queue(self):
        r = self.run_case([event(1, '01.11.2026')], args=['--subject-id', '2', '--check'])
        self.assertEqual(r['result'], 0)
        self.assertEqual(r['joined'], [])
        self.assertFalse(any(x[1].endswith('/join') for x in r['requests']))
        self.assertIn('Подходящих очередей для новой записи: 1.', r['log'])

    def test_check_empty_queue_finishes(self):
        r = self.run_case([], args=['--subject-id', '2', '--check'])
        self.assertEqual(r['result'], 0)
        self.assertEqual(r['cycles'], 1)

    def test_check_disabled_and_existing_are_excluded(self):
        r = self.run_case([event(20, allowed=False)], mine=[event(20)], args=['--subject-id', '2', '--check'])
        self.assertEqual(r['result'], 0)
        self.assertIn('Подходящих очередей для новой записи нет.', r['log'])
        self.assertNotIn('Слежу', r['log'])
        self.assertNotIn('запрос', r['log'])

    def test_check_shows_account_and_finished_queue(self):
        own = dict(event(20, 'Основы криптографии 31.10.2026', allowed=False), myStatus=3)
        r = self.run_case([own], mine=[own], args=['--check'])
        self.assertEqual(r['result'], 0)
        self.assertIn('Вход выполнен: test@example.invalid', r['log'])
        self.assertIn('«Основы криптографии 31.10.2026» — завершил сдачу', r['log'])
        self.assertEqual(r['joined'], [])

    def test_check_shows_empty_account_queues(self):
        r = self.run_case([], args=['--check'])
        self.assertIn('У тебя пока нет текущих очередей.', r['log'])

    def test_own_queue_status_and_position(self):
        for status, label in [(0, 'сейчас на проверке'), (1, 'ожидаешь в очереди'),
                              (2, 'пропущен'), (3, 'завершил сдачу'),
                              ('Waiting', 'ожидаешь в очереди'), (None, 'статус не указан')]:
            with self.subTest(status=status):
                text = s.describe_mine(dict(event(1), myStatus=status, myPosition=4))
                self.assertIn(label, text)
                self.assertIn('место: 4', text)

    def test_check_network_error_is_failure(self):
        r = self.run_case([], args=['--check'], replies={'/api/me/queue-events?includePast=false': (500, {})})
        self.assertEqual(r['result'], 1)

    def test_missing_subject_fails_check_without_join(self):
        r = self.run_case([event(1, 'Дата')], args=['--subject-id', '2', '--check'], replies={'/api/queues/1': (200, {})})
        self.assertEqual(r['result'], 1)
        self.assertEqual(r['joined'], [])

if __name__ == '__main__':
    unittest.main(verbosity=2)
