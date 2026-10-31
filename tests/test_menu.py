import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launch
import local_settings as settings


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.location = patch.object(settings, 'DATA_DIR', Path(self.temporary.name))
        self.location.start()
        self.addCleanup(self.location.stop)

    def menu(self, answers, result=0):
        output = io.StringIO()
        with patch('builtins.input', side_effect=answers), patch.object(launch.getpass, 'getpass', return_value='dummy-secret'), patch.object(launch.sniper, 'main', side_effect=result if isinstance(result, list) else None, return_value=result) as run, contextlib.redirect_stdout(output):
            self.assertEqual(launch.main(), 0)
        return run, output.getvalue()

    def test_encryption_round_trip_and_forget(self):
        settings.save_credentials('student@example.invalid', 'dummy-secret')
        raw = (settings.DATA_DIR / 'account.bin').read_bytes()
        self.assertNotIn(b'dummy-secret', raw)
        self.assertNotIn(b'student@example.invalid', raw)
        self.assertEqual(settings.load_credentials(), ('student@example.invalid', 'dummy-secret'))
        settings.forget_credentials()
        self.assertIsNone(settings.load_credentials())

    def test_corrupt_credentials_give_helpful_error(self):
        (settings.DATA_DIR / 'account.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'пункте 3'):
            settings.load_credentials()

    def test_interval_persists_and_accepts_comma(self):
        self.assertEqual(settings.load_interval(), 5)
        self.assertEqual(settings.save_interval('2,5'), 2.5)
        self.assertEqual(settings.load_interval(), 2.5)

    def test_invalid_intervals_do_not_overwrite(self):
        settings.save_interval(7)
        for value in ('0', '-1', 'nan', 'inf', 'abc', '0,5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.save_interval(value)
        self.assertEqual(settings.load_interval(), 7)

    def test_saved_account_reused_across_modes(self):
        run, _ = self.menu(['3', '1', 'student@example.invalid', '0', '1', '2', '0'])
        self.assertEqual(run.call_count, 2)
        self.assertIn('--check', run.call_args_list[0].args[0])
        self.assertNotIn('--check', run.call_args_list[1].args[0])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['credentials'], ('student@example.invalid', 'dummy-secret'))
            self.assertNotIn('dummy-secret', call.args[0])

    def test_auto_save_on_first_check_and_return_to_menu(self):
        run, text = self.menu(['1', 'student@example.invalid', '1', '0'])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(text.count('QSniper —'), 3)

    def test_saved_account_reused_on_next_start(self):
        self.menu(['3', '1', 'student@example.invalid', '0', '0'])
        run, _ = self.menu(['1', '0'])
        self.assertEqual(run.call_count, 1)

    def test_interval_used_by_check_and_monitor(self):
        settings.save_credentials('student@example.invalid', 'dummy-secret')
        run, _ = self.menu(['4', '3,5', '1', '2', '0'])
        for call in run.call_args_list:
            args = call.args[0]
            self.assertEqual(args[args.index('--interval') + 1], '3.5')

    def test_interrupt_returns_to_menu(self):
        settings.save_credentials('student@example.invalid', 'dummy-secret')
        run, text = self.menu(['2', '1', '0'], result=[KeyboardInterrupt(), 0])
        self.assertEqual(run.call_count, 2)
        self.assertIn('Возврат в главное меню', text)

    def test_error_returns_to_menu(self):
        settings.save_credentials('student@example.invalid', 'dummy-secret')
        run, text = self.menu(['1', '0'], result=1)
        self.assertIn('с ошибкой', text)

    def test_preview_does_not_call_server(self):
        with patch.object(launch.sniper, 'alarm') as alarm:
            run, _ = self.menu(['5', '0'])
        alarm.assert_called_once()
        run.assert_not_called()

    def test_account_menu_displays_saved_data_without_changing(self):
        settings.save_credentials('student@example.invalid', 'dummy-secret')
        run, text = self.menu(['3', '0', '0'])
        self.assertIn('Почта: student@example.invalid', text)
        self.assertIn('Пароль: dummy-secret', text)
        self.assertEqual(settings.load_credentials(), ('student@example.invalid', 'dummy-secret'))
        run.assert_not_called()

    def test_account_menu_change_updates_display(self):
        settings.save_credentials('old@example.invalid', 'old-dummy')
        _, text = self.menu(['3', '1', 'new@example.invalid', '0', '0'])
        self.assertIn('Почта: old@example.invalid', text)
        self.assertIn('Почта: new@example.invalid', text)
        self.assertEqual(settings.load_credentials(), ('new@example.invalid', 'dummy-secret'))


if __name__ == '__main__':
    unittest.main()
