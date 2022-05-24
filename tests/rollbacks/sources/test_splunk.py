from unittest import mock

import pytest
import splunklib.client

from sticht.rollbacks.sources.splunk import MAX_QUERY_TIME_S
from sticht.rollbacks.sources.splunk import SplunkMetricWatcher


def test_query_calls_process_results():
    watcher = SplunkMetricWatcher(
        label='test_query',
        query='what does it all mean',
        on_failure_callback=lambda _: None,
        splunk_host='splarnk.yelp.com',
        splunk_port=1234,
        credentials_callback=lambda: ('username', 'totally_a_password'),
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


def test__get_splunk_result_respects_query_timeout():
    with mock.patch(
        'time.sleep',
        return_value=None,
    ), mock.patch(
        'sticht.rollbacks.sources.splunk.splunklib.client.Job',
        autospec=True,
    ) as mock_job:
        # make sure we hit the timeout...
        mock_job.is_done.side_effect = [False] * (MAX_QUERY_TIME_S * 2)
        watcher = SplunkMetricWatcher(
            label='test_query',
            query='what does it all mean',
            on_failure_callback=lambda _: None,
            splunk_host='splarnk.yelp.com',
            splunk_port=1234,
            credentials_callback=lambda: ('username', 'totally_a_password'),
        )
        watcher._splunk = mock.Mock(spec=splunklib.client.Service)

        assert watcher._get_splunk_results(mock_job) is None
        mock_job.cancel.assert_called_once()


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
            splunk_host='splarnk.yelp.com',
            splunk_port=1234,
            credentials_callback=lambda: ('username', 'totally_a_password'),
        )
        assert watcher._get_splunk_results(mock_job) == expected_results