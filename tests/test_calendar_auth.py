from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Event, Thread, current_thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from console.calendar_service import CalendarService
from console.local_data import Storage


class CalendarAuthTests(unittest.TestCase):
    NOW = 1_800_000_000
    OWNERS = [
        {'id': 'synthetic-owner-a', 'email': 'owner-a@example.test'},
        {'id': 'synthetic-owner-b', 'email': 'owner-b@example.test'},
        {'id': 'synthetic-owner-c', 'email': 'owner-c@example.test'},
    ]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = Storage(Path(temporary.name) / 'calendar.json')
        self.service = CalendarService(self.storage, 'http://127.0.0.1:8765')
        self.service.configure({'installed': {
            'client_id': 'synthetic-client.apps.googleusercontent.com',
            'client_secret': 'synthetic-client-secret',
        }})
        self.service.fetch = Mock(side_effect=AssertionError('Unexpected external request'))
        timer = patch('console.calendar_service.time.time', return_value=self.NOW)
        timer.start()
        self.addCleanup(timer.stop)
        self.seed_accounts([self.account(owner) for owner in self.OWNERS])

    def account(self, owner=None, **overrides):
        owner = owner or self.OWNERS[0]
        return {
            **owner,
            'access_token': 'cached-access-' + owner['id'],
            'refresh_token': 'stored-refresh-' + owner['id'],
            'refresh_token_received_at': self.NOW - 1000,
            'expires_at': self.NOW + 3600,
            **overrides,
        }

    def seed_accounts(self, accounts):
        self.storage.update(lambda data: data.update(accounts=deepcopy(accounts)))

    def restrict(self, **policies):
        self.storage.update(lambda data: data.update(allowedAccounts=deepcopy(self.OWNERS), **policies))

    def accounts(self):
        return {account['id']: account for account in self.storage.read()['accounts']}

    def grant(self, refresh_token='newly-issued-refresh'):
        result = {'scope': CalendarService.SCOPES, 'access_token': 'callback-access', 'expires_in': 1200}
        if refresh_token is not None:
            result['refresh_token'] = refresh_token
        return result

    def userinfo(self, owner=None, **overrides):
        owner = owner or self.OWNERS[0]
        return {'sub': owner['id'], 'email': owner['email'], 'email_verified': True, **overrides}

    def prepare_callback(self, tokens, info=None):
        query = parse_qs(urlparse(self.service.begin()).query)
        self.service.fetch = Mock(side_effect=[tokens, info or self.userinfo()])
        return query['state'][0]

    def test_owner_callback_accepts_each_bound_pair_and_records_new_refresh_receipt(self):
        self.restrict(requireNewRefreshToken=True)
        for owner in self.OWNERS:
            with self.subTest(owner=owner['id']):
                state = self.prepare_callback(self.grant('issued-' + owner['id']), self.userinfo(owner))
                self.service.callback(state, 'synthetic-code')
                saved = self.accounts()[owner['id']]
                self.assertEqual(saved['refresh_token'], 'issued-' + owner['id'])
                self.assertEqual(saved['refresh_token_received_at'], self.NOW)
                self.assertEqual(saved['expires_at'], self.NOW + 1200)
        public = self.service.public_config()
        self.assertTrue(public['ownerOnly'])
        self.assertCountEqual(public['accounts'], self.OWNERS)

    def test_owner_callback_rejects_unbound_ids_emails_and_crossed_owner_pairs_without_writing(self):
        self.restrict()
        invalid = [
            {'sub': 'synthetic-outsider', 'email': self.OWNERS[0]['email']},
            {'sub': self.OWNERS[0]['id'], 'email': 'outsider@example.test'},
            {'sub': self.OWNERS[0]['id'], 'email': self.OWNERS[1]['email']},
        ]
        for identity in invalid:
            with self.subTest(identity=identity):
                before = self.storage.read()
                state = self.prepare_callback(self.grant(), self.userinfo(**identity))
                with self.assertRaises(ValueError):
                    self.service.callback(state, 'synthetic-code')
                self.assertEqual(self.storage.read(), before)
                self.assertNotIn(state, self.service.pending)

    def test_owner_callback_requires_email_verified_to_be_exactly_true(self):
        self.restrict()
        for verified in [False, None, 'true', 1, 'missing']:
            with self.subTest(verified=verified):
                info = self.userinfo(email_verified=verified)
                if verified == 'missing':
                    info.pop('email_verified')
                before = self.storage.read()
                state = self.prepare_callback(self.grant(), info)
                with self.assertRaises(ValueError):
                    self.service.callback(state, 'synthetic-code')
                self.assertEqual(self.storage.read(), before)

    def test_public_config_and_events_filter_outsiders_without_harming_bound_accounts(self):
        self.restrict()
        owners = [self.account(owner) for owner in self.OWNERS]
        outsider = self.account({'id': 'synthetic-outsider', 'email': 'outsider@example.test'})
        crossed = self.account({**self.OWNERS[0], 'email': self.OWNERS[1]['email']})
        self.seed_accounts([outsider, crossed, *owners])
        public = self.service.public_config()
        self.assertTrue(public['ownerOnly'])
        self.assertCountEqual(public['accounts'], self.OWNERS)
        allowed_tokens = {account['access_token'] for account in owners}

        def fetch(url, data=None, token=None):
            self.assertIn(token, allowed_tokens)
            if '/users/me/calendarList?' in url:
                return {'items': [{'id': 'synthetic-calendar', 'summary': 'Synthetic calendar'}]}
            return {'items': [{'id': 'synthetic-event', 'summary': 'Synthetic event',
                               'start': {'date': '2026-01-01'}, 'end': {'date': '2026-01-02'}}]}

        # Duplicate subject IDs are not normal persisted input. Keep the crossed
        # identity for public filtering above, then test real account isolation.
        self.seed_accounts([outsider, *owners])
        self.service.fetch = Mock(side_effect=fetch)
        result = self.service.events('2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z')
        self.assertEqual(len(result['events']), len(self.OWNERS))
        self.assertEqual(len(result['calendars']), len(self.OWNERS))
        self.assertEqual(result['errors'], [])
        self.assertCountEqual(result['accounts'], self.OWNERS)
        self.assertNotIn(outsider['email'], str(result))
        self.assertEqual(self.service.fetch.call_count, len(self.OWNERS) * 2)

    def test_owner_token_rechecks_persisted_identity_before_cached_or_forced_access(self):
        self.restrict()
        stale = self.account()
        self.seed_accounts([{**stale, 'email': self.OWNERS[1]['email']}])
        for force in [False, True]:
            with self.subTest(force_refresh=force):
                with self.assertRaises(ValueError):
                    self.service.token(stale, force_refresh=force)
        self.service.fetch.assert_not_called()

    def test_force_refresh_bypasses_access_cache_and_persists_rotation_for_next_refresh(self):
        self.restrict(requireNewRefreshToken=True)
        original = self.accounts()[self.OWNERS[0]['id']]
        untouched = deepcopy(self.accounts()[self.OWNERS[1]['id']])
        self.assertEqual(self.service.token(original), original['access_token'])
        self.service.fetch.assert_not_called()
        self.service.fetch.side_effect = [
            {'access_token': 'forced-access', 'refresh_token': 'rotated-refresh', 'expires_in': 1800},
            {'access_token': 'second-forced-access', 'expires_in': 900},
        ]
        self.assertEqual(self.service.token(original, force_refresh=True), 'forced-access')
        request = self.service.fetch.call_args.args
        self.assertEqual(request[0], 'https://oauth2.googleapis.com/token')
        self.assertEqual(request[1]['grant_type'], 'refresh_token')
        self.assertEqual(request[1]['refresh_token'], original['refresh_token'])
        saved = self.accounts()[original['id']]
        self.assertEqual(saved['refresh_token'], 'rotated-refresh')
        self.assertEqual(saved['refresh_token_received_at'], self.NOW)
        self.assertEqual(saved['expires_at'], self.NOW + 1800)
        self.assertEqual(self.service.token(original), 'forced-access')
        self.assertEqual(self.service.fetch.call_count, 1)
        self.assertEqual(self.service.token(original, force_refresh=True), 'second-forced-access')
        self.assertEqual(self.service.fetch.call_args.args[1]['refresh_token'], 'rotated-refresh')
        self.assertEqual(self.accounts()[original['id']]['refresh_token'], 'rotated-refresh')
        self.assertEqual(self.accounts()[self.OWNERS[1]['id']], untouched)

    def test_expired_normal_refresh_preserves_refresh_token_when_google_does_not_rotate_it(self):
        self.restrict(requireNewRefreshToken=True)
        expired = self.account(expires_at=self.NOW - 1)
        self.seed_accounts([expired])
        self.service.fetch.return_value = {'access_token': 'refreshed-access', 'expires_in': 600}
        self.service.fetch.side_effect = None
        self.assertEqual(self.service.token(expired), 'refreshed-access')
        saved = self.accounts()[expired['id']]
        self.assertEqual(saved['refresh_token'], expired['refresh_token'])
        self.assertEqual(saved['refresh_token_received_at'], expired['refresh_token_received_at'])
        self.assertEqual(saved['expires_at'], self.NOW + 600)
        self.assertEqual(self.service.fetch.call_args.args[1]['grant_type'], 'refresh_token')

    def test_callback_reauthorization_survives_an_inflight_forced_refresh_without_rotation(self):
        self.restrict(requireNewRefreshToken=True)
        previous = self.account(authorized_at=self.NOW - 2000)
        others = [self.account(owner) for owner in self.OWNERS[1:]]
        self.seed_accounts([previous, *others])
        state = self.prepare_callback(self.grant('callback-new-refresh'))
        auth_started, refresh_started, release_auth, release_refresh, checkpoint = [Event() for _ in range(5)]
        errors = []

        def wait_for(event, description):
            self.assertTrue(event.wait(3), description)

        def fetch(url, data=None, token=None):
            if data is not None and data.get('grant_type') == 'authorization_code':
                auth_started.set()
                wait_for(release_auth, 'authorization response was not released')
                return self.grant('callback-new-refresh')
            if data is not None and data.get('grant_type') == 'refresh_token':
                self.assertEqual(data['refresh_token'], previous['refresh_token'])
                refresh_started.set()
                wait_for(release_refresh, 'refresh response was not released')
                return {'access_token': 'inflight-refreshed-access', 'expires_in': 900}
            self.assertEqual(url, 'https://www.googleapis.com/oauth2/v3/userinfo')
            return self.userinfo()

        def run(operation):
            try:
                operation()
            except Exception as error:
                errors.append(error)

        callback = Thread(target=run, args=(lambda: self.service.callback(state, 'synthetic-code'),), daemon=True)
        refresh = Thread(target=run, args=(lambda: self.service.token(previous, force_refresh=True),), daemon=True)
        lock = self.service.lock

        class CheckpointLock:
            def __enter__(self):
                if current_thread() is callback and refresh_started.is_set():
                    checkpoint.set()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        update = self.storage.update

        def observed_update(fn):
            result = update(fn)
            if current_thread() is callback:
                checkpoint.set()
            return result

        self.service.lock = CheckpointLock()
        self.service.fetch = Mock(side_effect=fetch)
        with patch.object(self.storage, 'update', side_effect=observed_update):
            try:
                callback.start()
                wait_for(auth_started, 'callback did not reach authorization exchange')
                refresh.start()
                wait_for(refresh_started, 'forced refresh did not read the old account')
                release_auth.set()
                # With serialization callback waits for the refresh's lock. On
                # the broken implementation it has already saved new credentials.
                wait_for(checkpoint, 'callback did not reach its final save')
            finally:
                release_auth.set()
                release_refresh.set()
                for worker in [callback, refresh]:
                    if worker.ident is not None:
                        worker.join(3)
            self.assertFalse(callback.is_alive(), 'callback thread did not finish')
            self.assertFalse(refresh.is_alive(), 'refresh thread did not finish')

        self.assertEqual(errors, [])
        saved = self.accounts()
        account = saved[previous['id']]
        self.assertEqual(account['refresh_token'], 'callback-new-refresh')
        self.assertEqual(account['refresh_token_received_at'], self.NOW)
        self.assertEqual(account['authorized_at'], self.NOW)
        self.assertEqual(account['access_token'], 'callback-access')
        self.assertEqual(account['expires_at'], self.NOW + 1200)
        for other in others:
            self.assertEqual(saved[other['id']], other)

    def test_failed_forced_refresh_keeps_previous_credentials(self):
        self.restrict()
        account = self.accounts()[self.OWNERS[0]['id']]
        before = self.storage.read()
        self.service.fetch.side_effect = ValueError('Synthetic refresh rejection')
        with self.assertRaises(ValueError):
            self.service.token(account, force_refresh=True)
        self.assertEqual(self.storage.read(), before)

    def test_migration_without_a_new_refresh_token_cannot_fall_back_or_replace_old_account(self):
        self.restrict(requireNewRefreshToken=True)
        for refresh in [None, '']:
            with self.subTest(refresh_token=refresh):
                before = self.storage.read()
                state = self.prepare_callback(self.grant(refresh))
                with self.assertRaises(ValueError):
                    self.service.callback(state, 'synthetic-code')
                self.assertEqual(self.storage.read(), before)
                self.assertNotIn(state, self.service.pending)
                self.service.fetch.assert_called_once()

    def test_general_callback_keeps_existing_refresh_and_receipt_time_without_new_refresh(self):
        for has_receipt_time in [True, False]:
            with self.subTest(previous_receipt_time=has_receipt_time):
                old = self.account()
                if not has_receipt_time:
                    old.pop('refresh_token_received_at')
                self.seed_accounts([old])
                info = self.userinfo()
                info.pop('email_verified')
                state = self.prepare_callback(self.grant(None), info)
                self.service.callback(state, 'synthetic-code')
                saved = self.accounts()[old['id']]
                self.assertEqual(saved['access_token'], 'callback-access')
                self.assertEqual(saved['refresh_token'], old['refresh_token'])
                self.assertEqual(saved.get('refresh_token_received_at'), old.get('refresh_token_received_at'))
                self.assertFalse(self.service.public_config()['ownerOnly'])


if __name__ == '__main__':
    unittest.main()
