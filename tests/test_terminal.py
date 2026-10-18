import base64
from pathlib import Path
import tempfile
import time
import unittest

from console.terminal_service import TerminalService


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wokyis-test-', dir='/tmp')
        self.service = TerminalService(self.temp.name, self.temp.name)
        self.viewer = 'a' * 32
        if not self.service.tmux:
            self.skipTest('tmux not installed')

    def tearDown(self):
        self.service.detach_all()
        if self.service.tmux:
            self.service._run('kill-server', check=False)
        self.temp.cleanup()

    def until(self, ident, needle):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            out = self.service.output(ident, 0, '', self.viewer)
            if needle in base64.b64decode(out['data']):
                return out
            time.sleep(.05)
        self.fail('Expected shell output did not arrive')

    def test_shell_state_survives_detach_restart_and_close_is_isolated(self):
        one, two = self.service.create()['id'], self.service.create()['id']
        self.service.open(one, self.viewer)
        self.service.post('input', {'viewer': self.viewer, 'id': one, 'data': "export WOKYIS_TEST_VALUE=kept; printf 'ONE_%s_END\\n' 42\r"})
        out = self.until(one, b'ONE_42_END')
        self.assertTrue(out['reset'])
        delta = self.service.output(one, out['cursor'], out['stream'], self.viewer)
        self.assertFalse(delta['reset'])
        self.service.post('resize', {'viewer': self.viewer, 'id': one, 'cols': 70, 'rows': 16})
        self.service.post('input', {'viewer': self.viewer, 'id': one, 'data': 'stty size\r'})
        self.until(one, b'16 70')
        self.service.detach_all()
        self.service = TerminalService(self.temp.name, self.temp.name)
        self.assertEqual({r['id'] for r in self.service.list()['sessions']}, {one, two})
        self.service.open(one, self.viewer)
        self.service.post('input', {'viewer': self.viewer, 'id': one, 'data': "printf 'STATE_%s_END\\n' \"$WOKYIS_TEST_VALUE\"; stty size\r"})
        self.until(one, b'STATE_kept_END')
        self.service.post('close', {'id': one})
        self.assertEqual([r['id'] for r in self.service.list()['sessions']], [two])
        with self.assertRaises(ValueError):
            self.service.post('input', {'viewer': self.viewer, 'id': one, 'data': 'echo gone\r'})

    def test_invalid_target_never_reaches_tmux_and_resize_is_bounded(self):
        for value in ['*', 'all', '-a', '../terminal', None, 42]:
            with self.assertRaises(ValueError):
                self.service.open(value, self.viewer)
        ident = self.service.create()['id']
        self.service.open(ident, self.viewer)
        with self.assertRaises(ValueError):
            self.service.post('resize', {'viewer': self.viewer, 'id': ident, 'cols': 10000, 'rows': 20})
        with self.assertRaises(ValueError):
            self.service.post('input', {'viewer': self.viewer, 'id': ident, 'data': 'x' * 32769})

    def test_each_viewer_has_its_own_pty_and_detaching_keeps_the_shell(self):
        ident = self.service.create()['id']
        other = 'b' * 32
        self.service.open(ident, self.viewer)
        self.service.open(ident, other)
        a = self.service.client(ident, self.viewer)
        b = self.service.client(ident, other)
        self.assertNotEqual(a.tty, b.tty)
        self.assertNotEqual(a.stream, b.stream)
        self.service.post('detach', {'viewer': self.viewer})
        with self.assertRaises(ValueError):
            self.service.client(ident, self.viewer)
        self.assertIs(self.service.client(ident, other), b)
        self.assertEqual(len(self.service.list()['sessions']), 1)
        self.service.open(ident, self.viewer)
        self.assertNotEqual(self.service.client(ident, self.viewer).stream, a.stream)
        self.service.post('input', {'viewer': self.viewer, 'id': ident, 'data': "printf 'VIEWER_%s_END\\n' fresh\r"})
        self.until(ident, b'VIEWER_fresh_END')
        # A cursor from the previous PTY must replay the new PTY's handshake/output.
        result = self.service.output(ident, 10**9, a.stream, self.viewer)
        self.assertIn(b'VIEWER_fresh_END', base64.b64decode(result['data']))

    def test_old_output_cursor_requests_fresh_redraw(self):
        ident = self.service.create()['id']
        self.service.open(ident, self.viewer)
        client = self.service.client(ident, self.viewer)
        with client.lock:
            client.start += len(client.buffer) + 100
            client.buffer = bytearray(b'old incomplete escape sequence')
        result = self.service.output(ident, 0, client.stream, self.viewer)
        self.assertTrue(result['reset'])
        self.assertEqual(result['data'], '')


if __name__ == '__main__':
    unittest.main()
