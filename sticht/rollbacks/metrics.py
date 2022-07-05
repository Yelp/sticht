import logging
import threading
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import splunklib.client
import yaml

from sticht.rollbacks.soaconfigs import get_cluster_from_soaconfigs_filename
from sticht.rollbacks.soaconfigs import get_rollback_files_from_soaconfigs
from sticht.rollbacks.sources.splunk import create_splunk_metricwatchers
from sticht.rollbacks.types import MetricWatcher
from sticht.rollbacks.types import SplunkAuth

log = logging.getLogger(__name__)


def _get_metric_configs_for_service_by_cluster(
    service: str,
    soa_dir: str,
) -> Dict[str, Dict[str, Any]]:  # TODO: add type for rollback file config
    configs = {}
    for filename in get_rollback_files_from_soaconfigs(soa_dir, service=service):
        with open(filename, 'r') as file:
            configs[get_cluster_from_soaconfigs_filename(filename)] = yaml.safe_load(file)
    return configs


def watch_metrics_for_service(
    service: str,
    soa_dir: str,
    on_failure_callback: Callable[[str, Optional[bool]], None],
    on_failure_trigger_callback: Callable[[bool], None],
    splunk_auth: SplunkAuth,
) -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    threads: List[threading.Thread] = []
    watchers: List[MetricWatcher] = []

    failing = False
    splunk = None

    def callback_wrapper(watcher: 'MetricWatcher') -> None:
        nonlocal failing
        old_failing = failing
        new_failing = any(w.failing for w in watchers)
        on_failure_callback(watcher.label, watcher.failing)

        failing = new_failing

        if new_failing == (not old_failing):
            on_failure_trigger_callback(new_failing)

    for cluster, config in _get_metric_configs_for_service_by_cluster(service, soa_dir).items():
        log.info(f'Processing configs for {service} in {cluster}...')

        rollback_conditions = config.get('conditions')
        check_interval_s = config.get('check_interval_s')
        if not rollback_conditions:
            log.warning(f'{cluster} has a rollback file - but no conditions!')
            continue

        splunk_conditions = rollback_conditions.get('splunk')
        if splunk_conditions:
            print(splunk_auth)
            if splunk is None:
                splunk = splunklib.client.connect(
                    host=splunk_auth.host,
                    port=splunk_auth.port,
                    username=splunk_auth.username,
                    password=splunk_auth.password,
                )

            watchers.extend(
                create_splunk_metricwatchers(
                    splunk_conditions=splunk_conditions,
                    check_interval_s=check_interval_s,
                    on_failure_callback=callback_wrapper,
                    splunk=splunk,
                ),
            )
    return threads, watchers
