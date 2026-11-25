from datetime import datetime
from datetime import timezone
from unittest import mock

from sticht.alertmanager import AlertmanagerClient
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher
from sticht.rollbacks.sources.alertmanager import AllAlertCallback
from sticht.rollbacks.sources.alertmanager import IndividualAlertCallback
from sticht.rollbacks.sources.alertmanager import MISSED_POLLS_BEFORE_RESOLVED


TEST_ALERTMANAGER_URL = 'http://alertmanager.example.com'
TEST_FILTERS = [['alertname=HighLatency', 'service=myapp']]
TEST_DEPLOY_START_TIME = 1000.0


def _make_watcher(individual_alert_callback=None, all_alert_callback=None, filters=None):
    watcher = AlertManagerWatcher(
        alertmanager_url=TEST_ALERTMANAGER_URL,
        filters=filters or TEST_FILTERS,
        individual_alert_callback=individual_alert_callback or mock.Mock(spec=IndividualAlertCallback),
        all_alert_callback=all_alert_callback or mock.Mock(spec=AllAlertCallback),
        deploy_start_time=TEST_DEPLOY_START_TIME,
    )
    watcher._client = mock.create_autospec(AlertmanagerClient, instance=True)
    return watcher


def _ts_iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _make_alert(alertname, starts_at_epoch, dry_run=False, fingerprint=None):
    labels = {'alertname': alertname}
    if dry_run:
        labels['paasta_rollback_dry_run'] = 'true'
    return {
        'labels': labels,
        'startsAt': _ts_iso(starts_at_epoch),
        'fingerprint': fingerprint or f'{alertname}-fingerprint',
    }


def poll_until_resolved(watcher):
    for _ in range(MISSED_POLLS_BEFORE_RESOLVED):
        watcher.process_result([])


def test_process_result_new_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([_make_alert('HighLatency', 1500.0)])

    individual_cb.assert_called_once_with('HighLatency', failing=True)
    all_cb.assert_called_once_with(failing=True)
    assert watcher.active_alerts == {'HighLatency'}


def test_process_result_updates_active_alerts_before_callbacks():
    # NOTE: each callback records what active_alerts looks like at the moment it runs - i.e., what another
    # thread would see if it checked mid-callback.
    # This is a regression test: previously, both recordings would be empty sets since we updated
    # active_alerts *after* calling the callbacks (which could lead to a fun race condition).
    active_alerts_during_callbacks = []
    watcher = _make_watcher(
        individual_alert_callback=lambda *args, **kwargs: active_alerts_during_callbacks.append(watcher.active_alerts),
        all_alert_callback=lambda **kwargs: active_alerts_during_callbacks.append(watcher.active_alerts),
    )

    watcher.process_result([_make_alert('HighLatency', 1500.0)])

    assert active_alerts_during_callbacks == [{'HighLatency'}, {'HighLatency'}]


def test_process_result_excludes_pre_deploy_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    # alert before deploy is excluded
    watcher.process_result([_make_alert('OldAlert', 500.0)])
    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == set()
    assert watcher.preexisting_alert_names == {'OldAlert'}

    # alert with unparseable startsAt defaults to deploy_start_time (NOT excluded)
    watcher.process_result([{'labels': {'alertname': 'BadTimestamp'}, 'startsAt': 'garbage', 'fingerprint': 'bad'}])
    individual_cb.assert_called_once_with('BadTimestamp', failing=True)


def test_process_result_resolved_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)
    watcher.process_result([_make_alert('OldAlert', 1500.0)])
    individual_cb.reset_mock()
    all_cb.reset_mock()

    poll_until_resolved(watcher)

    individual_cb.assert_called_once_with('OldAlert', failing=False)
    all_cb.assert_called_once_with(failing=False)
    assert watcher.active_alerts == set()


def test_process_result_no_change_no_callbacks():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([])

    individual_cb.assert_not_called()
    all_cb.assert_not_called()


def test_process_result_dry_run_alert_does_not_trigger_all_callback():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([_make_alert('DryRunAlert', 1500.0, dry_run=True)])

    individual_cb.assert_called_once_with('DryRunAlert', failing=True, dry_run=True)
    all_cb.assert_not_called()
    assert watcher.active_dry_run_alerts == {'DryRunAlert'}
    assert watcher.active_alerts == set()


def test_process_result_mix_of_dry_run_and_real_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([
        _make_alert('RealAlert', 1500.0),
        _make_alert('DryRunAlert', 1500.0, dry_run=True),
    ])

    individual_cb.assert_any_call('RealAlert', failing=True)
    individual_cb.assert_any_call('DryRunAlert', failing=True, dry_run=True)
    all_cb.assert_called_once_with(failing=True)
    assert watcher.active_alerts == {'RealAlert'}
    assert watcher.active_dry_run_alerts == {'DryRunAlert'}


def test_process_result_dry_run_alert_resolved():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)
    watcher.process_result([_make_alert('DryRunAlert', 1500.0, dry_run=True)])
    individual_cb.reset_mock()

    poll_until_resolved(watcher)

    individual_cb.assert_called_once_with('DryRunAlert', failing=False, dry_run=True)
    all_cb.assert_not_called()


def test_query_skips_partial_results_on_api_error():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(
        individual_alert_callback=individual_cb,
        all_alert_callback=all_cb,
        filters=[['group=one'], ['group=two']],
    )
    watcher._client.fetch_alerts.side_effect = [
        [_make_alert('HighLatency', 1500.0)],
        [_make_alert('HighErrors', 1500.0)],
    ]
    watcher.query()
    individual_cb.reset_mock()
    all_cb.reset_mock()

    # if a filter group fails, its alerts shouldn't be treated as resolved
    watcher._client.fetch_alerts.side_effect = [[_make_alert('HighLatency', 1500.0)], Exception('oh no')]
    watcher.query()

    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == {'HighLatency', 'HighErrors'}


def test_process_result_preexisting_alert_ignored_even_if_starts_at_changes():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    # AlertManager can report a newer startsAt for an alert that's been firing the whole time
    watcher.process_result([_make_alert('OldAlert', 500.0)])
    watcher.process_result([_make_alert('OldAlert', 1500.0)])
    watcher.process_result([_make_alert('OldAlert', 500.0)])

    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == set()


def test_process_result_preexisting_alert_that_recovers_and_refires_is_failing():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([_make_alert('OldAlert', 500.0)])
    poll_until_resolved(watcher)
    watcher.process_result([_make_alert('OldAlert', 1500.0)])

    individual_cb.assert_called_once_with('OldAlert', failing=True)
    all_cb.assert_called_once_with(failing=True)
    assert watcher.active_alerts == {'OldAlert'}


def test_process_result_same_alertname_different_fingerprints():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    # a pre-existing alert shouldn't mask a new one that happens to share its alertname
    watcher.process_result([
        _make_alert('HighErrors', 500.0, fingerprint='old'),
        _make_alert('HighErrors', 1500.0, fingerprint='new'),
    ])

    individual_cb.assert_called_once_with('HighErrors', failing=True)
    all_cb.assert_called_once_with(failing=True)
    assert watcher.active_alerts == {'HighErrors'}


def test_process_result_alert_briefly_missing_is_not_resolved():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)
    watcher.process_result([_make_alert('HighErrors', 1500.0)])
    individual_cb.reset_mock()
    all_cb.reset_mock()

    # AlertManager can briefly drop a continuously-firing alert - that shouldn't count as a recovery
    for _ in range(MISSED_POLLS_BEFORE_RESOLVED - 1):
        watcher.process_result([])
    watcher.process_result([_make_alert('HighErrors', 1500.0)])
    for _ in range(MISSED_POLLS_BEFORE_RESOLVED - 1):
        watcher.process_result([])

    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == {'HighErrors'}

    watcher.process_result([])

    individual_cb.assert_called_once_with('HighErrors', failing=False)
    all_cb.assert_called_once_with(failing=False)
    assert watcher.active_alerts == set()


def test_process_result_preexisting_alert_briefly_missing_stays_preexisting():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    # this is the flapping pattern we've seen in practice: the alert drops out of AlertManager for a poll and
    # then comes back with a fresh startsAt
    watcher.process_result([_make_alert('OldAlert', 500.0)])
    watcher.process_result([])
    watcher.process_result([_make_alert('OldAlert', 1500.0)])

    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == set()


def test_first_poll_done_is_set_by_a_processed_poll():
    watcher = _make_watcher()
    assert not watcher.first_poll_done.is_set()

    watcher.process_result([])

    assert watcher.first_poll_done.is_set()


def test_first_poll_done_is_set_when_the_poll_fails():
    watcher = _make_watcher()
    watcher._client.fetch_alerts.side_effect = Exception('alertmanager is down')

    watcher.query()

    # a failed poll still ends the wait, and doesn't count as any alerts having been seen
    assert watcher.first_poll_done.is_set()
    assert not watcher.has_preexisting_alerts


def test_has_preexisting_alerts_and_cleared_callback():
    cleared_cb = mock.Mock()
    watcher = _make_watcher()
    watcher.preexisting_alerts_cleared_callback = cleared_cb
    assert not watcher.has_preexisting_alerts

    watcher.process_result([_make_alert('OldAlert', 500.0)])
    assert watcher.has_preexisting_alerts
    cleared_cb.assert_not_called()

    # a single missed poll isn't enough for the alert to count as resolved
    watcher.process_result([])
    assert watcher.has_preexisting_alerts
    cleared_cb.assert_not_called()

    poll_until_resolved(watcher)
    assert not watcher.has_preexisting_alerts
    cleared_cb.assert_called_once_with()

    # ...and it only fires when something actually cleared
    watcher.process_result([])
    cleared_cb.assert_called_once_with()


def test_cleared_callback_waits_for_every_preexisting_alert():
    cleared_cb = mock.Mock()
    watcher = _make_watcher()
    watcher.preexisting_alerts_cleared_callback = cleared_cb
    watcher.process_result([_make_alert('OldA', 500.0), _make_alert('OldB', 500.0)])

    # OldA resolves but OldB keeps firing
    for _ in range(MISSED_POLLS_BEFORE_RESOLVED):
        watcher.process_result([_make_alert('OldB', 500.0)])
    assert watcher.has_preexisting_alerts
    cleared_cb.assert_not_called()

    poll_until_resolved(watcher)
    cleared_cb.assert_called_once_with()


def test_preexisting_dry_run_alerts_do_not_count():
    cleared_cb = mock.Mock()
    watcher = _make_watcher()
    watcher.preexisting_alerts_cleared_callback = cleared_cb

    watcher.process_result([_make_alert('OldDryRun', 500.0, dry_run=True)])

    # still listed as pre-existing, but they don't count
    assert watcher.preexisting_alert_names == {'OldDryRun'}
    assert not watcher.has_preexisting_alerts
    cleared_cb.assert_not_called()

    poll_until_resolved(watcher)
    assert watcher.preexisting_alert_names == set()
    assert watcher.preexisting_dry_run_fingerprints == set()
    cleared_cb.assert_not_called()


def test_new_alerts_never_count_as_preexisting():
    watcher = _make_watcher()

    watcher.process_result([_make_alert('NewAlert', 1500.0)])

    assert not watcher.has_preexisting_alerts
