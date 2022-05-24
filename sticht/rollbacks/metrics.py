import threading
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple


class MetricWatcher:
    """
    Base class for the different classes of metric sources that will be used
    for automatic rollbacks
    """
    # TODO: figure out contents of this class in a more thought-out way

    def __init__(self, label: str, on_failure_callback: Callable[['MetricWatcher'], None]) -> None:
        # is the metric in question currently failing? (None == unknown)
        self.failing: Optional[bool] = None
        # was the metric failing *before* the deployment began? (None == unknown)
        self.previously_failing: Optional[bool] = None
        # how should we refer to this metric in Slack?
        self.label = label
        # how do we notify that a metric is newly failing?
        self.on_failure_callback = on_failure_callback

    def query(self) -> None:
        """
        Part of the public interface for a MetricWatcher.
        Should send the configured query to the relevant metric source and
        compare it against the configured thresholds (and, if failing, invoke
        the callback held by this class)
        """
        raise NotImplementedError

    def process_result(self, result: Any) -> None:
        """
        Part of the public interface for a MetricWatcher.
        Will be called by query() with some data which should be compared
        against the configured thresholds (and, if failing, invoke the callback
        held by this class)
        """
        raise NotImplementedError


def watch_metrics_for_service(service: str, soa_dir: str) -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    threads: List[threading.Thread] = []
    watchers: List[MetricWatcher] = []

    return threads, watchers
