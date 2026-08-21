import logging
import threading
import time
from datetime import datetime
from datetime import timezone
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from typing_extensions import Protocol

import sticht.metrics as metrics
from sticht.alertmanager import Alert
from sticht.alertmanager import AlertmanagerClient

log = logging.getLogger(__name__)

_DEFAULT_CHECK_INTERVAL_S = 30
# XXX: should we maybe have this be passed down from paasta so that it's not hardcoded here?
_DRY_RUN_LABEL = 'paasta_rollback_dry_run'
METRICS_INTERFACE_BASE_NAME = 'sticht.alertmanager'


def _parse_iso_timestamp(ts: str) -> float:
    ts = ts.replace('Z', '+00:00')
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp()


class IndividualAlertCallback(Protocol):
    def __call__(self, label: str, *, failing: bool, dry_run: bool = False) -> None: ...


class AllAlertCallback(Protocol):
    def __call__(self, *, failing: bool) -> None: ...


class AlertManagerWatcher:
    """Polls AlertManager for firing alerts, ignoring those that predate the deploy.

    Args:
        alertmanager_url: Base URL for the target AlertManager - e.g., alertmanager.mycoolcompany.com
        filters: AlertManager filter matchers (e.g. ['alertname=HighLatency', 'service=myapp']) - at Yelp, callers of
            sticht will pre-populate the expected filters (e.g., PaaSTA will pass down all the various filters that
            we're expecting to have alerts match on).
            Multiple filter groups can be passed to achieve OR semantics - each group is queried separately and results
            are ORed.
        individual_alert_callback: callback function that takes two parameters: a label for the alert in question
            as well a boolean representing if that alert is active or not.
            the label is used purely for notifying users what alerts are now firing (or no longer firing) based on the
            boolean value
        all_alert_callback: callback function that takes a single boolean representing if any alert is failing.
            if so, the expectation is that this callback will potentially trigger a state machine transition towards
            the failing or recovered states (e.g., more or less like what the *_slo_callback functions do)
        deploy_start_time: Unix timestamp; alerts firing before this time are treated as pre-existing and ignored
            (unless they recover mid-deploy).
        check_interval_s: how often (in seconds) to poll AlertManager for alerts
        extra_monitoring_labels: optional dict of key-value pairs attached to metrics, enabling
            deploy group or service grouping in monitoring dashboards.
    """

    def __init__(
        self,
        alertmanager_url: str,
        filters: List[List[str]],
        individual_alert_callback: IndividualAlertCallback,
        all_alert_callback: AllAlertCallback,
        extra_monitoring_labels: Optional[Dict[str, str]] = None,
        # XXX: our CEP also includes some tunables for how many polls alerts need to be firing/not-firing for
        # that we'll want to add here later
        check_interval_s: int = _DEFAULT_CHECK_INTERVAL_S,
        deploy_start_time: Optional[float] = None,
    ) -> None:
        self.filters = filters
        self.check_interval_s = check_interval_s
        self.deploy_start_time = deploy_start_time if deploy_start_time is not None else time.time()
        self.extra_monitoring_labels = extra_monitoring_labels if extra_monitoring_labels is not None else {}
        self.active_alerts: set[str] = set()
        self.active_dry_run_alerts: set[str] = set()
        self.individual_alert_callback = individual_alert_callback
        self.all_alert_callback = all_alert_callback
        self._client = AlertmanagerClient(alertmanager_url, extra_monitoring_labels)

    def query(self) -> None:
        with metrics.create_timer(
            f'{METRICS_INTERFACE_BASE_NAME}.alertmanager_poll_duration_ms',
            default_dimensions=self.extra_monitoring_labels,
        ):
            all_alerts: List[Alert] = []
            api_errors = 0
            for filter_group in self.filters:
                try:
                    all_alerts.extend(self._client.fetch_alerts(filters=filter_group))
                except Exception:
                    api_errors += 1
                    log.exception(
                        f'Error fetching alerts from AlertManager for filter group {filter_group}, '
                        f'continuing with remaining filter groups',
                    )

            metrics.create_counter(
                f'{METRICS_INTERFACE_BASE_NAME}.alertmanager_api_errors',
                default_dimensions=self.extra_monitoring_labels,
            ).count(api_errors)

            self.process_result(all_alerts)
            metrics.create_counter(
                f'{METRICS_INTERFACE_BASE_NAME}.alertmanager_alerts_checked',
                default_dimensions=self.extra_monitoring_labels,
            ).count(len(all_alerts))

    def process_result(
        self,
        alerts: List['Alert'],
    ) -> None:
        # NOTE: this is just tracking alert names for now - we can store the whole payload if necessary later on
        # ...but then we'll definitely need to change the set shenanigans below if we just swap things in-place here
        alerts_seen: set[str] = set()
        dry_run_alerts_seen: set[str] = set()

        for alert in alerts:
            try:
                starts_at = _parse_iso_timestamp(alert['startsAt'])
            except (KeyError, ValueError):
                starts_at = self.deploy_start_time
                log.warning(
                    f'Could not parse startsAt for alert, assuming alert was triggered during the deployment: {alert}',
                )

            if starts_at < self.deploy_start_time:
                # XXX: print message about excluded alert?
                continue

            alertname = alert['labels']['alertname']
            if alert['labels'].get(_DRY_RUN_LABEL) == 'true':
                dry_run_alerts_seen.add(alertname)
            else:
                alerts_seen.add(alertname)

        # notify about newly failing dry-run alerts (informational only)
        for alert in dry_run_alerts_seen - self.active_dry_run_alerts:
            self.individual_alert_callback(alert, failing=True, dry_run=True)
        for alert in self.active_dry_run_alerts - dry_run_alerts_seen:
            self.individual_alert_callback(alert, failing=False, dry_run=True)
        self.active_dry_run_alerts = dry_run_alerts_seen

        # ping users about newly failing alerts
        new_alerts = alerts_seen - self.active_alerts
        for alert in new_alerts:
            self.individual_alert_callback(alert, failing=True)

        # ...and then about alerts that have since recovered
        resolved_alerts = self.active_alerts - alerts_seen
        for alert in resolved_alerts:
            self.individual_alert_callback(alert, failing=False)

        # ...and then potentially transition through the state machine if there's been any alert changes
        if self.active_alerts != alerts_seen:
            self.all_alert_callback(failing=len(alerts_seen) > 0)

        # ...and then finally: store the current alerts for the next iteration to compare to
        self.active_alerts = alerts_seen

    def watch(self) -> None:
        while True:
            self.query()
            time.sleep(self.check_interval_s)


def watch_alertmanager_alerts(
    alertmanager_url: str,
    filters: List[List[str]],
    individual_alert_callback: IndividualAlertCallback,
    all_alert_callback: AllAlertCallback,
    extra_monitoring_labels: Optional[Dict[str, str]] = None,
    check_interval_s: int = _DEFAULT_CHECK_INTERVAL_S,
) -> Tuple[threading.Thread, AlertManagerWatcher]:
    watcher = AlertManagerWatcher(
        alertmanager_url=alertmanager_url,
        filters=filters,
        individual_alert_callback=individual_alert_callback,
        all_alert_callback=all_alert_callback,
        check_interval_s=check_interval_s,
        extra_monitoring_labels=extra_monitoring_labels,
    )
    thread = threading.Thread(target=watcher.watch, daemon=True)
    thread.start()
    return thread, watcher
