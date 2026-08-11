from typing import Dict
from typing import List
from typing import Sequence
from typing import TypedDict

import requests

DEFAULT_ALERTMANAGER_TIMEOUT_S = 60


class AlertStatus(TypedDict):
    state: str
    silencedBy: List[str]
    inhibitedBy: List[str]
    mutedBy: List[str]


class Receiver(TypedDict):
    name: str


class Alert(TypedDict):
    labels: Dict[str, str]
    annotations: Dict[str, str]
    receivers: List[Receiver]
    fingerprint: str
    startsAt: str
    updatedAt: str
    endsAt: str
    generatorURL: str
    status: AlertStatus


class BaseAlertParams(TypedDict):
    silenced: bool
    inhibited: bool
    unprocessed: bool


class AlertQueryParams(BaseAlertParams):
    filter: Sequence[str]


DEFAULT_ALERT_PARAMS: BaseAlertParams = {
    'silenced': False,
    'inhibited': False,
    'unprocessed': False,
}


class AlertmanagerError(Exception):
    """
    Exception class for alertmanager error
    """

    def __init__(self, message: str, extra_details: str) -> None:
        super().__init__(message)
        self.extra_details = extra_details


class AlertmanagerClient:
    def __init__(
        self,
        alertmanager_url: str,
        timeout: int = DEFAULT_ALERTMANAGER_TIMEOUT_S,
    ) -> None:
        self.alertmanager_url = alertmanager_url.rstrip('/')
        self.timeout = timeout

    def _send_request(
        self,
        path: str,
        params: AlertQueryParams,
    ) -> List[Alert]:
        url = f"{self.alertmanager_url}/api/v2/{path.strip('/')}"
        headers = {
            'User-Agent': 'sticht',
        }
        resp = requests.get(
            url=url,
            # types-requests doesn't recognize TypedDicts as valid params
            params=params,  # type: ignore[arg-type]
            headers=headers,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AlertmanagerError(
                f'Error while retrieving response from alertmanager: {resp.text}',
                f'StatusCode: {resp.status_code!r}, Response: {resp.content!r}',
            )
        return resp.json()

    def fetch_alerts(
        self,
        # NOTE: you can technically fetch alerts with no filters
        # ...but for our current usecases, that's not something
        # we'll ever do
        filters: Sequence[str],
    ) -> List[Alert]:
        return self._send_request(
            path='/alerts',
            params={**DEFAULT_ALERT_PARAMS, 'filter': filters},
        )
