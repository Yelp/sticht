from typing import Any
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Union

import requests


class AlertmanagerError(Exception):
    """
    Exception class for alertmanager error
    """

    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


class AlertmanagerClient:
    def __init__(
        self,
        alertmanager_url: str,
        timeout: int = 60,
    ) -> None:
        self.alertmanager_url = alertmanager_url
        self.timeout = timeout

    def _make_params(
        self,
        filters: Optional[Sequence[str]] = None,
        params: Optional[Mapping[str, str]] = None,
    ) -> Mapping[str, Union[str, Sequence[str]]]:
        parameters: Dict[str, Union[str, Sequence[str]]] = {}
        # create filter parameter using specified filters
        if filters is not None:
            parameters['filter'] = filters
        if params is not None:
            parameters.update(params)

        return parameters

    def _make_matchers_json(
        self, filters: Optional[Sequence[str]],
    ) -> List[Dict[str, object]]:
        matchers: List[Dict[str, object]] = []
        if filters is None:
            return matchers
        for each_filter in filters:
            if '!~' in each_filter:
                key, value = each_filter.split('!~')
                is_regex = True
                equals = False
            elif '=~' in each_filter:
                key, value = each_filter.split('=~')
                is_regex = True
                equals = True
            elif '!=' in each_filter:
                key, value = each_filter.split('!=')
                is_regex = False
                equals = False
            elif '=' in each_filter:
                key, value = each_filter.split('=')
                is_regex = False
                equals = True
            else:
                raise AlertmanagerError(
                    'Invalid filter',
                    f'Filter: {each_filter!r}',
                )
            matchers.append(
                {'isEqual': equals, 'isRegex': is_regex, 'name': key, 'value': value},
            )
        return matchers

    def _make_request(
        self,
        path: str,
        params: Optional[Mapping[str, Union[str, Sequence[str]]]] = None,
        json: Any = None,
        request_type: str = 'GET',
    ) -> Any:
        url = f"{self.alertmanager_url}/api/v2/{path.strip('/')}"
        headers = {
            'User-Agent': 'sticht',
        }

        if request_type == 'GET':
            resp = requests.get(
                url=url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        elif request_type == 'POST':
            resp = requests.post(
                url=url,
                headers=headers,
                json=json,
                timeout=self.timeout,
            )
        elif request_type == 'DELETE':
            resp = requests.delete(
                url=url,
                headers=headers,
                timeout=self.timeout,
            )
            return resp
        else:
            raise AlertmanagerError(
                'Unknown request type',
                f'Request type: {request_type!r}',
            )
        if resp.status_code != 200:
            raise AlertmanagerError(
                f'Error while retrieving response from alertmanager: {resp.text}',
                f'StatusCode: {resp.status_code!r}, Response: {resp.content!r}',
            )
        return resp.json()

    def fetch_alerts(
        self,
        filters: Optional[Sequence[str]] = None,
        params: Optional[Mapping[str, str]] = None,
    ) -> Any:
        return self._make_request(
            path='/alerts', params=self._make_params(filters, params),
        )

    def cluster_status(self) -> Any:
        return self._make_request(path='/status')

    def fetch_silences(
        self,
        filters: Optional[Sequence[str]] = None,
    ) -> Any:
        return self._make_request(path='/silences', params=self._make_params(filters))
