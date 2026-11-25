import asyncio
from unittest import mock

import pytest

from sticht.rollbacks.base import RollbackSlackDeploymentProcess
from sticht.rollbacks.slo import SLOWatcher
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher
from sticht.slack import SlackDeploymentProcess
from tests.test_slack import DummySlackDeploymentProcess


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


class DummyRollbackSlackDeploymentProcess(RollbackSlackDeploymentProcess, DummySlackDeploymentProcess):
    def get_signalfx_api_token(self):
        return 'token'

    def auto_rollbacks_enabled(self):
        return True

    def get_auto_rollback_delay(self):
        return 0.01


def test_cancel_auto_rollback_countdown_only_cancels_rollback_countdowns():
    process = DummyRollbackSlackDeploymentProcess()
    with mock.patch.object(process, 'notify_users', autospec=True), \
            mock.patch.object(process, 'trigger', autospec=True) as mock_trigger:
        process.start_auto_rollback_countdown('rollback_slo_failure', extra_text='')
        process.start_auto_rollback_countdown('rollback_alertmanager_failure', extra_text='')
        process.start_timer(0.02, 'auto_certify', 'certify')

        # one condition recovering only cancels its own countdown...
        assert process.cancel_auto_rollback_countdown('rollback_slo_failure')
        # ...so the other one is still there to be cancelled along with every other rollback countdown
        assert process.cancel_auto_rollback_countdown()

        process.event_loop.run_until_complete(asyncio.sleep(0.05))

    # ...while non-rollback timers are left alone
    assert mock_trigger.call_args_list == [mock.call('auto_certify')]


class GatedDeployProcess(DummyRollbackSlackDeploymentProcess):
    """A tiny deploy: _begin -> deploying -> done, with the alert gate opted in."""

    def __init__(self, gate_enabled=True):
        self.gate_enabled = gate_enabled
        self.visited_states = []
        super().__init__()

    def alert_gate_enabled(self):
        return self.gate_enabled

    def alert_gate_release_state(self):
        return 'deploying'

    def after_state_change(self):
        self.visited_states.append(self.state)
        super().after_state_change()

    def status_code_by_state(self):
        return {'done': 0}

    def states(self):
        return ['_begin', 'deploying', 'done']

    def valid_transitions(self):
        yield {'source': '_begin', 'dest': 'deploying', 'trigger': 'start_deploy'}
        yield {'source': 'deploying', 'dest': 'done', 'trigger': 'finish_deploy'}

    def start_transition(self):
        return 'start_deploy'


def _make_gated_process(first_poll_done=True, preexisting=True, enabled=True):
    process = GatedDeployProcess(gate_enabled=enabled)
    process.alertmanager_watcher = mock.Mock(spec=AlertManagerWatcher)
    process.alertmanager_watcher.first_poll_done = mock.Mock(wait=mock.Mock(return_value=first_poll_done))
    process.alertmanager_watcher.has_preexisting_alerts = preexisting
    return process


def _run_start(process):
    """Starts the deploy, or holds it at the gate."""
    with mock.patch.object(process, 'notify_users', autospec=True):
        process.event_loop.run_until_complete(process.before_start())
        if process.state == '_begin':
            process.trigger(process.start_transition())


def test_alert_gate_is_off_by_default():
    assert RollbackSlackDeploymentProcess.alert_gate_enabled(mock.Mock(spec=RollbackSlackDeploymentProcess)) is False


def test_alert_gate_holds_deploy_while_preexisting_alerts_fire():
    process = _make_gated_process()

    _run_start(process)

    assert process.state == 'awaiting_alerts_clear'


def test_alert_gate_releases_when_alerts_clear():
    process = _make_gated_process()
    _run_start(process)

    process.preexisting_alerts_cleared_callback()

    assert process.state == 'deploying'


def test_alert_gate_releases_on_proceed_button():
    process = _make_gated_process()
    _run_start(process)
    assert 'proceed' in process.get_available_buttons()

    process.trigger('proceed_button_clicked')

    assert process.state == 'deploying'


def test_alert_gate_releases_straight_into_the_release_state():
    process = _make_gated_process()
    _run_start(process)

    process.preexisting_alerts_cleared_callback()

    # it doesn't go back through the start state
    assert process.visited_states == ['awaiting_alerts_clear', 'deploying']


def test_alert_gate_adds_no_state_when_disabled():
    process = _make_gated_process(enabled=False)

    assert 'awaiting_alerts_clear' not in process.machine.states
    assert 'proceed' not in process.get_available_buttons()


def test_alert_gate_release_state_must_be_overridden():
    with pytest.raises(NotImplementedError):
        RollbackSlackDeploymentProcess.alert_gate_release_state(mock.Mock(spec=RollbackSlackDeploymentProcess))


def test_alert_gate_message_uses_the_agreed_wording():
    process = _make_gated_process()
    with mock.patch.object(process, 'update_slack_thread', autospec=True) as mock_thread:
        _run_start(process)

    mock_thread.assert_called_once_with(
        'Alerts are currently firing and auto-rollbacks will not work for this deploy until they resolve - '
        'continue deploying anyway?',
        color='danger',
    )


def test_cleared_outside_the_gate_is_a_noop():
    process = _make_gated_process(preexisting=False)
    _run_start(process)
    assert process.state == 'deploying'

    process.preexisting_alerts_cleared_callback()

    assert process.state == 'deploying'


@pytest.mark.parametrize(
    'kwargs',
    [
        {'enabled': False},  # not opted in
        {'first_poll_done': False},  # first poll failed or was too slow: fail-open
        {'preexisting': False},  # nothing to wait for
    ],
)
def test_alert_gate_does_not_hold_deploy(kwargs):
    process = _make_gated_process(**kwargs)

    _run_start(process)

    assert process.state == 'deploying'


def test_alert_gate_does_not_hold_deploy_without_a_watcher():
    process = _make_gated_process()
    process.alertmanager_watcher = None

    _run_start(process)

    assert process.state == 'deploying'
