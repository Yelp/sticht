from unittest import mock

import pytest

from sticht.rollbacks.base import RollbackSlackDeploymentProcess
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher


def _get_alertmanager_text(active_alerts, preexisting_alert_names):
    watcher = mock.Mock(
        spec=AlertManagerWatcher,
        active_alerts=active_alerts,
        preexisting_alert_names=preexisting_alert_names,
    )
    process = mock.Mock(spec=RollbackSlackDeploymentProcess, alertmanager_watcher=watcher)
    # state is added at runtime by the state machine, so it isn't part of the spec
    process.state = 'deploying'
    process.is_terminal_state.return_value = False
    return RollbackSlackDeploymentProcess.get_alertmanager_text(process, summary=False)


@pytest.mark.parametrize(
    'active_alerts,preexisting_alert_names,expected',
    [
        (set(), set(), ':ok_hand: No AlertManager alerts firing.'),
        ({'NewAlert'}, set(), ':alert: 1 AlertManager alert(s) firing:\n NewAlert\n'),
        (
            set(),
            {'OldAlert'},
            ':grimacing: 1 AlertManager alert(s) were firing before deploy, and will be ignored:\n OldAlert\n',
        ),
        (
            # an alertname that's both firing and pre-existing should only be listed as firing
            {'NewAlert'},
            {'NewAlert', 'OldAlert'},
            ':alert: 1 AlertManager alert(s) firing:\n NewAlert\n '
            ':grimacing: 1 AlertManager alert(s) were firing before deploy, and will be ignored:\n OldAlert\n',
        ),
    ],
)
def test_get_alertmanager_text(active_alerts, preexisting_alert_names, expected):
    assert _get_alertmanager_text(active_alerts, preexisting_alert_names) == expected
