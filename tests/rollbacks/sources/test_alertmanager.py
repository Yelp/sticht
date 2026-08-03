from datetime import datetime
from datetime import timezone
from unittest import mock

from sticht.alertmanager import AlertmanagerClient
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher
from sticht.rollbacks.sources.alertmanager import AllAlertCallback
from sticht.rollbacks.sources.alertmanager import IndividualAlertCallback


TEST_ALERTMANAGER_URL = 'http://alertmanager.example.com'
TEST_FILTERS = [['alertname=HighLatency', 'service=myapp']]
TEST_DEPLOY_START_TIME = 1000.0


def _make_watcher(individual_alert_callback=None, all_alert_callback=None):
    watcher = AlertManagerWatcher(
        alertmanager_url=TEST_ALERTMANAGER_URL,
        filters=TEST_FILTERS,
        individual_alert_callback=individual_alert_callback or mock.Mock(spec=IndividualAlertCallback),
        all_alert_callback=all_alert_callback or mock.Mock(spec=AllAlertCallback),
        deploy_start_time=TEST_DEPLOY_START_TIME,
    )
    watcher._client = mock.Mock(spec=AlertmanagerClient)
    return watcher


def _ts_iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _make_alert(alertname, starts_at_epoch, dry_run=False):
    labels = {'alertname': alertname}
    if dry_run:
        labels['paasta_rollback_dry_run'] = 'true'
    return {'labels': labels, 'startsAt': _ts_iso(starts_at_epoch)}


def test_process_result_new_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    watcher.process_result([_make_alert('HighLatency', 1500.0)])

    individual_cb.assert_called_once_with('HighLatency', failing=True)
    all_cb.assert_called_once_with(failing=True)
    assert watcher.active_alerts == {'HighLatency'}


def test_process_result_excludes_pre_deploy_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)

    # alert before deploy is excluded
    watcher.process_result([_make_alert('OldAlert', 500.0)])
    individual_cb.assert_not_called()
    all_cb.assert_not_called()
    assert watcher.active_alerts == set()

    # alert with unparseable startsAt defaults to deploy_start_time (NOT excluded)
    watcher.process_result([{'labels': {'alertname': 'BadTimestamp'}, 'startsAt': 'garbage'}])
    individual_cb.assert_called_once_with('BadTimestamp', failing=True)


def test_process_result_resolved_alerts():
    individual_cb = mock.Mock(spec=IndividualAlertCallback)
    all_cb = mock.Mock(spec=AllAlertCallback)
    watcher = _make_watcher(individual_alert_callback=individual_cb, all_alert_callback=all_cb)
    watcher.active_alerts = {'OldAlert'}

    watcher.process_result([])

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
    watcher.active_dry_run_alerts = {'DryRunAlert'}

    watcher.process_result([])

    individual_cb.assert_called_once_with('DryRunAlert', failing=False, dry_run=True)
    all_cb.assert_not_called()
