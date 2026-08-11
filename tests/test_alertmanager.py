from unittest import mock

from sticht.alertmanager import AlertmanagerClient
from sticht.alertmanager import DEFAULT_ALERT_PARAMS


def test_alerts_with_filters():
    client = AlertmanagerClient(alertmanager_url='a_url')
    some_filters = ('a', 'b')
    with mock.patch.object(client, '_send_request', autospec=True):
        client.fetch_alerts(filters=some_filters)
        client._send_request.assert_called_with(
            path='/alerts',
            params={**DEFAULT_ALERT_PARAMS, 'filter': some_filters},
        )
