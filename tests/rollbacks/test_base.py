from unittest import mock

import pytest

from sticht.rollbacks.base import RollbackSlackDeploymentProcess
from sticht.rollbacks.slo import SLOWatcher
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher
from sticht.slack import SlackDeploymentProcess


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


# NOTE: this test is kinda funky: it's really just here to serve as a regression test/warning
# for the funky things we're doing here
# ...which we should maybe consider not doing :p
def test_init_does_not_clobber_watchers_started_by_subclass():
    alertmanager_watcher = mock.Mock(spec=AlertManagerWatcher)
    slo_watchers = [mock.Mock(spec=SLOWatcher)]

    # HACK:  i don't really want to have to define all of the actual abstract methods for this regression test
    # ...so let's just patch them out ;)
    with mock.patch.object(RollbackSlackDeploymentProcess, '__abstractmethods__', frozenset()):
        class MockRollbackProcess(RollbackSlackDeploymentProcess):
            def __init__(self):
                # like PaaSTA: start watchers *before* calling our superclass's constructor
                self.slo_watchers = slo_watchers
                self.alertmanager_watcher = alertmanager_watcher
                super().__init__()

    with mock.patch.object(SlackDeploymentProcess, '__init__', autospec=True, return_value=None):
        process = MockRollbackProcess()

    assert process.alertmanager_watcher is alertmanager_watcher
    assert process.slo_watchers is slo_watchers


@pytest.mark.parametrize(
    'alertmanager_rollbacks_enabled,auto_rollbacks_enabled,expected',
    [
        (False, True, False),
        (True, False, True),
    ],
)
def test_any_alertmanager_failing_is_independent_of_auto_rollbacks_enabled(
    alertmanager_rollbacks_enabled, auto_rollbacks_enabled, expected,
):
    process = mock.Mock(
        spec=RollbackSlackDeploymentProcess,
        alertmanager_watcher=mock.Mock(spec=AlertManagerWatcher, active_alerts={'NewAlert'}),
    )
    process.alertmanager_rollbacks_enabled.return_value = alertmanager_rollbacks_enabled
    process.auto_rollbacks_enabled.return_value = auto_rollbacks_enabled
    assert RollbackSlackDeploymentProcess.any_alertmanager_failing(process) is expected
