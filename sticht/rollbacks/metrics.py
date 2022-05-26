import logging
import threading
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Type
from typing import TypeVar

import yaml

from sticht.rollbacks.soaconfigs import get_cluster_from_soaconfigs_filename
from sticht.rollbacks.soaconfigs import get_rollback_files_from_soaconfigs

T = TypeVar('T', bound='MetricWatcher')

log = logging.getLogger(__name__)


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
        raise NotImplementedError()

    def process_result(self, result: Any) -> None:
        """
        Part of the public interface for a MetricWatcher.
        Will be called by query() with some data which should be compared
        against the configured thresholds (and, if failing, invoke the callback
        held by this class)
        """
        raise NotImplementedError()

    @classmethod
    def from_config(cls: Type[T], config: Dict[str, Any]) -> T:
        raise NotImplementedError()


def _get_metric_configs_for_service_by_cluster(service: str, soa_dir: str) -> Dict[str, Dict[str, Any]]:
    return {
        get_cluster_from_soaconfigs_filename(file): yaml.safe_load(file)
        for file in get_rollback_files_from_soaconfigs(soa_dir)
    }


def watch_metrics_for_service(service: str, soa_dir: str) -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    threads: List[threading.Thread] = []
    watchers: List[MetricWatcher] = []

    for cluster, config in _get_metric_configs_for_service_by_cluster(service, soa_dir).items():
        log.info(f'Processing configs for {service} in {cluster}...')

        rollback_conditions = config.get('conditions')
        if not rollback_conditions:
            log.warning(f'{cluster} has a rollback file - but no conditions!')
            continue

        splunk_conditions = rollback_conditions.get('splunk')
        if splunk_conditions:
            log.info(f'Creating {len(splunk_conditions)} Splunk watchers...')
            for splunk_rule in splunk_conditions:
                log.info(f"Creating Splunk watcher for: {splunk_rule['label']}")
                pass

    return threads, watchers
