import threading
from typing import List
from typing import Tuple

from sticht.rollbacks.types import MetricWatcher


def watch_metrics_for_service(service: str, soa_dir: str) -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    threads: List[threading.Thread] = []
    watchers: List[MetricWatcher] = []

    # TODO: actually implement :)
    return threads, watchers
