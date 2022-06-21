import logging
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

import splunklib.client
import splunklib.results

from sticht.rollbacks.metrics import MetricWatcher
from sticht.rollbacks.types import SplunkAuth


log = logging.getLogger(__name__)

DEFAULT_SPLUNK_POLL_S = 1
MAX_QUERY_TIME_S = 10


class SplunkMetricWatcher(MetricWatcher):
    def __init__(
        self,
        label: str,
        query: str,
        on_failure_callback: Callable[['MetricWatcher'], None],
        auth_callback: Callable[[], SplunkAuth],
    ) -> None:
        super().__init__(label, on_failure_callback)
        self._query = query
        # TODO: should we share a global version of this so that we're
        # not logging-in a million times?
        self._splunk: Optional[splunklib.client.Service] = None
        self._auth_callback = auth_callback

    def _splunk_login(self) -> None:
        auth = self._auth_callback()
        self._splunk = splunklib.client.connect(
            host=auth.host,
            port=auth.port,
            username=auth.username,
            password=auth.password,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SplunkMetricWatcher):
            return NotImplemented

        return (
            self.label,
            self._query,
        ) == (
            other.label,
            other._query,
        )

    def _get_splunk_results(
        self,
        job: splunklib.client.Job,
        # TODO: we can probably make somewhat of a TypedDict for what a result looks like?
    ) -> Optional[List[Dict[Any, Any]]]:
        result_total_wait_time_s = 0
        while not job.is_done():
            # TODO: should this timeout be hardcoded? dynamic? come from user config? something else?
            if result_total_wait_time_s > MAX_QUERY_TIME_S:
                log.error(f'Waited over {MAX_QUERY_TIME_S}s for query to finish - killing query.')
                job.cancel()
                # TODO: should this raise an exception instead?
                return None
            # TODO: should this sleep be hardcoded? dynamic? come from user config? something else?
            time.sleep(secs=DEFAULT_SPLUNK_POLL_S)
            result_total_wait_time_s += DEFAULT_SPLUNK_POLL_S

        results: List[Dict[Any, Any]] = []
        for result in splunklib.results.JSONResultsReader(
            stream=job.results(
                output_mode='json',
            ),
        ):
            # Diagnostic messages may be returned in the results
            if isinstance(result, splunklib.results.Message):
                log.debug(f'[splunk] {result.type}: {result.message}')
            # Normal events are returned as dicts
            elif isinstance(result, dict):
                results.append(result)

        return results

    # TODO: need to figure out what to do re: min. frequency here
    # since splunk searches can take a while
    # TODO: what if a query takes longer than the min. frequency?
    # do we just cut it off?
    def query(self) -> None:
        """
        Starts a Splunk search (i.e., Job) and polls it until its finished
        to get the results from a user-specified query
        """
        if not self._splunk:
            self._splunk_login()
            assert self._splunk is not None

        # TODO: do we need set set any other kwargs? e.g., earliest_time or output mode?
        job = self._splunk.search(query=self._query)
        results = self._get_splunk_results(job=job)
        self.process_result(results)

    def process_result(self, result: Optional[List[Dict[Any, Any]]]) -> None:
        """
        We allow users to compare either a single value from a query or the
        number of results from a query against configured thresholds to determine
        whether or not to rollback or not
        """
        pass
