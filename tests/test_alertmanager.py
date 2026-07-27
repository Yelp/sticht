from unittest import mock

from sticht.alertmanager import AlertmanagerClient


def test_alerts_all():
    """
    The fetch_alert() methods calls the /alerts url
    """
    client = AlertmanagerClient(alertmanager_url='a_url')
    with mock.patch.object(client, '_make_request'):
        client.fetch_alerts()
        client._send_request.assert_called_with(path='/alerts', params={})


def test_alerts_with_filters():
    """
    The filters argument of the fetch_alerts() methods results in a filter
    query parameter containing a list
    """
    client = AlertmanagerClient(alertmanager_url='a_url')
    some_filters = ('a', 'b')
    with mock.patch.object(client, '_make_request'):
        client.fetch_alerts(filters=some_filters)
        client._send_request.assert_called_with(
            path='/alerts', params={'filter': some_filters},
        )


def test_alerts_with_additional_params():
    """
    Additional parameters passed to the client.fetch_alerts() method result
    in corresponding additional query parameters along with the filter one
    """
    client = AlertmanagerClient(alertmanager_url='a_url')
    some_filters = ('a', 'b')
    additional_params = {'a': 'foo', 'b': 'bar'}
    expected_params = {**{'filter': some_filters}, **additional_params}
    with mock.patch.object(client, '_make_request'):
        client.fetch_alerts(filters=some_filters, params=additional_params)
        client._send_request.assert_called_with(path='/alerts', params=expected_params)


def test_fetch_silences():
    """
    Additional parameters passed to the client.fetch_silences() method result
    in corresponding additional query parameters along with the filter one
    """
    client = AlertmanagerClient(alertmanager_url='mocked_url')
    some_filters = ('a', 'b')
    expected_params = {**{'filter': some_filters}}
    with mock.patch.object(client, '_make_request'):
        client.fetch_silences(filters=some_filters)
        client._send_request.assert_called_with(
            path='/silences', params=expected_params,
        )
