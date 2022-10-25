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

DEFAULT_CHECK_INTERVAL_S = 30
MAX_QUERY_TIME_S = 10


class SplunkMetricWatcher(MetricWatcher):
    def __init__(
        self,
        label: str,
        query: str,
        query_type: str,
        on_failure_callback: Callable[['MetricWatcher'], None],
        auth_callback: Callable[[], SplunkAuth],
        check_interval_s=DEFAULT_CHECK_INTERVAL_S,
        upper_bound=None,
        lower_bound=None,
    ) -> None:
        super().__init__(label, on_failure_callback)
        self._query = query
        self._upper_bound = upper_bound
        self._lower_bound = lower_bound
        self._query_type = query_type
        # TODO: should we share a global version of this so that we're
        # not logging-in a million times?
        self._splunk: Optional[splunklib.client.Service] = None
        self._auth_callback = auth_callback
        self._check_interval_s = check_interval_s
        self.start_timestamp_seconds = time.time()
        self.bad_after_mark: Optional[bool] = None
        self.bad_before_mark: Optional[bool] = None
        self.failing: Optional[bool] = None

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
    def query(self, lookback_seconds=DEFAULT_CHECK_INTERVAL_S) -> None:
        """
        Starts a Splunk oneshot search (i.e., Job) and blocks until
        all results from a user-specified query are returned
        """
        if not self._splunk:
            self._splunk_login()
            assert self._splunk is not None

        earliest_timestamp_seconds = time.time() - lookback_seconds

        # Oneshot is a blocking search that runs immediately. It does not return a search job so there
        # is no need to poll for status. It directly returns the results of the search.
        # TODO: do we need set set any other kwargs? e.g., adhoc_search_level, earliest_time, rf, etc.
        res_reader = self._splunk.jobs.oneshot(
            query=self._query, output_mode='json',
            auto_cancel=MAX_QUERY_TIME_S, earliest_time=earliest_timestamp_seconds,
        )
        results = self._get_splunk_results(response_reader=res_reader)
        self.process_result(results, earliest_timestamp_seconds=earliest_timestamp_seconds)

    def process_result(self, result: Optional[List[Dict[Any, Any]]], earliest_timestamp_seconds) -> None:
        """
        We allow users to compare either a single value from a query or the
        number of results from a query against configured thresholds to determine
        whether or not to rollback or not
        """
        if earliest_timestamp_seconds > self.start_timestamp_seconds:
            self.bad_after_mark = self.is_window_bad(result)
        else:
            self.bad_before_mark = self.is_window_bad(result)

        old_failing = self.failing
        self.failing = self.bad_after_mark and not self.bad_before_mark

        if self.failing == (not old_failing):
            self.on_failure_callback(self)

    def is_window_bad(self, result) -> bool:
        # Results return an array | Number returns a number
        if self._query_type == 'results':
            result_num = len(result)
        elif self._query_type == 'number':
            result_num = next(iter(result[0].values())) if len(result) else len(result)

        if self._lower_bound and self._upper_bound:
            if result_num > self._lower_bound and result_num < self._upper_bound:
                return True
        elif self._lower_bound:
            if result_num > self._lower_bound:
                return True
        elif self._upper_bound:
            if result_num < self._upper_bound:
                return True

        return False

    @classmethod
    def from_config(  # type: ignore[override]
        # we don't care about violating LSP - we just want to follow a given interface
        cls: Type['SplunkMetricWatcher'],
        config: SplunkRule,
        on_failure_callback: Callable[['MetricWatcher'], None],
        auth_callback: Callable[[], SplunkAuth],
    ) -> 'SplunkMetricWatcher':
        return cls(
            config['label'],
            config['query'],
            config['query_type'],
            on_failure_callback=on_failure_callback,
            auth_callback=auth_callback,
            check_interval_s=config['check_interval_s'] if 'check_interval_s' in config else DEFAULT_CHECK_INTERVAL_S,
            lower_bound=config['lower_bound'] if 'lower_bound' in config else None,
            upper_bound=config['upper_bound'] if 'upper_bound' in config else None,
        )

    def watch(self) -> None:
        # TODO: Watch until bounce is complete OR timeout is passed
        while True:
            log.info(f'starting query for {self.label}')
            self.query(lookback_seconds=self._check_interval_s)

            log.info(f'Waiting {self._check_interval_s} before re-querying for {self.label}')
            time.sleep(self._check_interval_s)


def create_splunk_metricwatchers(
    splunk_conditions: List[SplunkRule],
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
                on_failure_callback=on_failure_callback,
                auth_callback=auth_callback,
            ),
        )

    return watchers
