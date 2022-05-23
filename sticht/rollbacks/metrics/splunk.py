import time
from sticht.rollbacks.metrics import MetricWatcher
from typing import Callable, Tuple, Optional
import splunklib.client
import splunklib.results

class SplunkMetricWatcher(MetricWatcher):
    def __init__(
        self,
        label: str,
        query: str,
        on_failure_callback: Callable[['MetricWatcher'], None],
        splunk_host: str,
        splunk_port: int,
        credentials_callback: Callable[[], Tuple[str, str]]
    ) -> None:
        super().__init__(label, on_failure_callback)
        self._query = query
        # TODO: should we share a global version of this so that we're
        # not logging in a million times?
        self._splunk: Optional[splunklib.client.Service] = None
        self._credentials_callback = credentials_callback
        self._splunk_host = splunk_host
        self._splunk_port = splunk_port

    def _splunk_login(self) -> None:
        user, password = self._credentials_callback()
        self.splunk = splunklib.client.connect(
            host=self._splunk_host,
            port=self._splunk_port,
            username=user,
            password=password,
        )

    # TODO: need to figure out what to do re: min. frequency here
    # since splunk searches can take a while
    # TODO: what if a query takes longer than the min. frequency?
    # do we just cut it off?
    def query(self) -> None:
        if not self._splunk:
            self._splunk_login()
            assert self._splunk

        # TODO: do we need set set any other kwargs? e.g., earliest_time or output mode?
        job = self._splunk.search(query=self._query)

        # TODO: how long do we actually want to wait?
        result_total_wait_time_s = 0
        # TODO: should this be hardcoded? dynamic? come from user config?
        result_poll_time_s = 1
        while not job.is_done():
            time.sleep(secs=result_poll_time_s)
            result_total_wait_time_s += result_poll_time_s

        result_reader = splunklib.results.JSONResultsReader(
            stream=job.results(
                output_mode='json',
            ),
        )

        for result in result_reader:
            if isinstance(result, splunklib.results.Message):
                # Diagnostic messages may be returned in the results
                logger.debug(f"[splunk] {result.type}: {result.message}")
            elif isinstance(result, dict):
                # Normal events are returned as dicts
                print(result)
        assert result_reader.is_preview == False
