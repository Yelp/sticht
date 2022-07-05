import logging
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Type

import splunklib.client
import splunklib.results

from sticht.rollbacks.types import MetricWatcher
from sticht.rollbacks.types import SplunkAuth
from sticht.rollbacks.types import SplunkRule


log = logging.getLogger(__name__)

DEFAULT_SPLUNK_POLL_S = 1
MAX_QUERY_TIME_S = 10


class SplunkMetricWatcher(MetricWatcher):
    def __init__(
        self,
        label: str,
        query: str,
        on_failure_callback: Callable[['MetricWatcher'], None],
        splunk:  SplunkAuth,
    ) -> None:
        super().__init__(label, on_failure_callback)
        self._query = query
        self._splunk = splunk

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
        # if not self._splunk:
        #     self._splunk_login()
        #     assert self._splunk is not None

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

    @classmethod
    def from_config(  # type: ignore[override]
        # we don't care about violating LSP - we just want to follow a given interface
        cls: Type['SplunkMetricWatcher'],
        config: SplunkRule,
        check_interval_s: Optional[float],
        on_failure_callback: Callable[['MetricWatcher'], None],
        splunk: SplunkAuth,
    ) -> 'SplunkMetricWatcher':
        if check_interval_s is not None:
            log.warning(
                f'Ignoring check_interval_s of {check_interval_s} and using default of {DEFAULT_SPLUNK_POLL_S}',
            )

        return cls(
            config['label'],
            config['query'],
            on_failure_callback=on_failure_callback,
            splunk=splunk,
        )


def create_splunk_metricwatchers(
    splunk_conditions: List[SplunkRule],
    check_interval_s: Optional[float],
    on_failure_callback: Callable[[MetricWatcher], None],
    splunk: SplunkAuth,
) -> List[SplunkMetricWatcher]:
    log.info(f'Creating {len(splunk_conditions)} Splunk watchers...')

    # if auth_callback is None:
    #     raise ValueError('Splunk rules defined, but no auth callback provided')

    watchers = []
    for splunk_rule in splunk_conditions:
        log.info(f"Creating Splunk watcher for: {splunk_rule['label']}")

        watchers.append(
            SplunkMetricWatcher.from_config(
                config=splunk_rule,
                check_interval_s=check_interval_s,
                on_failure_callback=on_failure_callback,
                splunk=splunk,
            ),
        )

    return watchers
