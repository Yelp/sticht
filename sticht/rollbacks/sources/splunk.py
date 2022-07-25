import logging
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
        response_reader: splunklib.binding.ResponseReader,
        # TODO: we can probably make somewhat of a TypedDict for what a result looks like?
    ) -> Optional[List[Dict[Any, Any]]]:
        results: List[Dict[Any, Any]] = []
        for result in splunklib.results.JSONResultsReader(stream=response_reader):
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
        Starts a Splunk oneshot search (i.e., Job) and polls it until its finished
        to get the results from a user-specified query
        """
        if not self._splunk:
            self._splunk_login()
            assert self._splunk is not None

        # TODO: do we need set set any other kwargs? e.g., adhoc_search_level, earliest_time, rf, etc.
        # Oneshot is a blocking search that runs immediately. It does not return a search job so there
        # is no need to poll for status. It directly returns the results of the search.
        rr = self._splunk.jobs.oneshot(query=self._query, output_mode='json', auto_cancel=MAX_QUERY_TIME_S)
        results = self._get_splunk_results(response_reader=rr)
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
        auth_callback: Callable[[], SplunkAuth],
    ) -> 'SplunkMetricWatcher':
        if check_interval_s is not None:
            log.warning(
                f'Ignoring check_interval_s of {check_interval_s} and using default of {DEFAULT_SPLUNK_POLL_S}',
            )

        return cls(
            config['label'],
            config['query'],
            on_failure_callback=on_failure_callback,
            auth_callback=auth_callback,
        )


def create_splunk_metricwatchers(
    splunk_conditions: List[SplunkRule],
    check_interval_s: Optional[float],
    on_failure_callback: Callable[[MetricWatcher], None],
    auth_callback: Optional[Callable[[], SplunkAuth]],
) -> List[SplunkMetricWatcher]:
    log.info(f'Creating {len(splunk_conditions)} Splunk watchers...')

    if auth_callback is None:
        raise ValueError('Splunk rules defined, but no auth callback provided')

    watchers = []
    for splunk_rule in splunk_conditions:
        log.info(f"Creating Splunk watcher for: {splunk_rule['label']}")

        watchers.append(
            SplunkMetricWatcher.from_config(
                config=splunk_rule,
                check_interval_s=check_interval_s,
                on_failure_callback=on_failure_callback,
                auth_callback=auth_callback,
            ),
        )

    return watchers
