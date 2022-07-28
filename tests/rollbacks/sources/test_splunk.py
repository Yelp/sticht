from unittest import mock

import pytest
import splunklib.client

from sticht.rollbacks.sources.splunk import SplunkMetricWatcher
from sticht.rollbacks.types import SplunkAuth

TEST_SPLUNK_AUTH = SplunkAuth(
    host='splank.yelp.com',
    port=1234,
    username='username',
    password='totally_a_password',
)


def test_query_calls_process_results():
    watcher = SplunkMetricWatcher(
        label='test_query',
        query='what does it all mean',
        on_failure_callback=lambda _: None,
        auth_callback=lambda: TEST_SPLUNK_AUTH,
    )
    watcher._splunk = mock.Mock(spec=splunklib.client.Service)

    with mock.patch(
        'sticht.rollbacks.sources.splunk.SplunkMetricWatcher._get_splunk_results',
        autospec=True,
    ), mock.patch(
        'sticht.rollbacks.sources.splunk.SplunkMetricWatcher.process_result',
        autospec=True,
    ) as mock_process_result:
        watcher.query()
        mock_process_result.assert_called_once()


@pytest.mark.parametrize(
    'results, expected_results', (
        (
            tuple(),
            [],
        ),
        (
            (splunklib.results.Message(type_='test', message='test'),),
            [],
        ),
        (
            (
                splunklib.results.Message(type_='test', message='test'),
                # TODO: this should probably look more like an actual query result...
                {'test': 'result'},
            ),
            [{'test': 'result'}],
        ),
    ),
)
def test__get_splunk_result(results, expected_results):
    with mock.patch(
        'time.sleep',
        return_value=None,
    ), mock.patch(
        'sticht.rollbacks.sources.splunk.splunklib.client.Job',
        autospec=True,
    ) as mock_job, mock.patch(
        'sticht.rollbacks.sources.splunk.splunklib.results.JSONResultsReader',
        autospec=True,
        return_value=results,
    ):
        mock_job.results.return_value = results
        watcher = SplunkMetricWatcher(
            label='test_query',
            query='what does it all mean',
            on_failure_callback=lambda _: None,
            auth_callback=lambda: TEST_SPLUNK_AUTH,
        )
        assert watcher._get_splunk_results(mock_job) == expected_results


def test_query_logins_on_first_attempt():
    watcher = SplunkMetricWatcher(
        label='test_query',
        query='what does it all mean',
        on_failure_callback=lambda _: None,
        auth_callback=lambda: TEST_SPLUNK_AUTH,
    )

    def _login_side_effect(_):
        watcher._splunk = mock.Mock(spec=splunklib.client.Service)

    with mock.patch(
        'sticht.rollbacks.sources.splunk.SplunkMetricWatcher._get_splunk_results',
        autospec=True,
    ), mock.patch(
        'sticht.rollbacks.sources.splunk.SplunkMetricWatcher.process_result',
        autospec=True,
    ), mock.patch(
        'sticht.rollbacks.sources.splunk.SplunkMetricWatcher._splunk_login',
        autospec=True,
        side_effect=_login_side_effect,
    ) as mock_login:
        watcher.query()
        mock_login.assert_called_once()


def test_login_calls_auth_callback():
    mock_credentials_callback = mock.Mock()
    watcher = SplunkMetricWatcher(
        label='test_query',
        query='what does it all mean',
        on_failure_callback=lambda _: None,
        auth_callback=mock_credentials_callback,
    )
    with mock.patch(
        'sticht.rollbacks.sources.splunk.splunklib.client.connect',
        autospec=True,
    ):
        watcher._splunk_login()
    mock_credentials_callback.assert_called_once()


@pytest.mark.parametrize(
    'check_interval_s', (
        None,
        0,
        123,
    ),
)
def test_from_config(check_interval_s):
    assert SplunkMetricWatcher.from_config(
        config={
            'label': 'label',
            'query': 'query',
        },
        check_interval_s=check_interval_s,
        on_failure_callback=lambda _: None,
        auth_callback=lambda: TEST_SPLUNK_AUTH,
    ) == SplunkMetricWatcher(
        label='label',
        query='query',
        on_failure_callback=lambda _: None,
        auth_callback=lambda: TEST_SPLUNK_AUTH,
    )
