import abc
from typing import List
from typing import Optional

from sticht.rollbacks.slo import SLOWatcher
from sticht.rollbacks.slo import watch_slos_for_service
from sticht.rollbacks.sources.alertmanager import _DEFAULT_CHECK_INTERVAL_S
from sticht.rollbacks.sources.alertmanager import AlertManagerWatcher
from sticht.rollbacks.sources.alertmanager import watch_alertmanager_alerts
from sticht.slack import Emoji
from sticht.slack import SlackDeploymentProcess


class RollbackSlackDeploymentProcess(SlackDeploymentProcess, abc.ABC):
    def __init__(self) -> None:
        self.slo_watchers: Optional[List[SLOWatcher]] = None
        self.alertmanager_watcher: Optional[AlertManagerWatcher] = None
        # normally you'd expect that this be called first thing in __init__,
        # but the way this class is constructed means that one of the methods called
        # by our superclass's constructor will throw when it tries to access the watcher
        # lists defined above...thus this fun (which actually mirrors how we later construct
        # a subclass of this class is PaaSTA!)
        # NOTE: this does mean that some degree of care must be taken when subclassing this class
        # as one could easily get into the same situation OR end up overwriting initializations done
        # by the subclass upon calling this constructor
        super().__init__()

    def get_extra_blocks_for_deployment(self):
        blocks = []
        slo_text = self.get_slo_text(summary=False)
        if slo_text:
            blocks.append(
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': slo_text}},
            )

        alertmanager_text = self.get_alertmanager_text(summary=False)
        if alertmanager_text:
            blocks.append(
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': alertmanager_text}},
            )
        return blocks

    def get_extra_summary_parts_for_deployment(self) -> List[str]:
        parts = super().get_extra_summary_parts_for_deployment()
        slo_text = self.get_slo_text(summary=True)
        if slo_text:
            parts.append(slo_text)

        alertmanager_text = self.get_alertmanager_text(summary=True)
        if alertmanager_text:
            parts.append(alertmanager_text)

        return parts

    def get_slo_text(self, summary: bool) -> str:
        if self.slo_watchers is not None and len(self.slo_watchers) > 0:
            failing = [w for w in self.slo_watchers if w.failing]

            if len(failing) > 0:
                slo_text_components = [
                    Emoji(':alert:'),
                    f'{len(failing)} of {len(self.slo_watchers)} SLOs are failing:\n',
                ]
                for slo_watcher in failing:
                    slo_text_components.append(f'{slo_watcher.label}\n')
            else:

                unknown = [
                    w
                    for w in self.slo_watchers
                    if w.bad_before_mark is None or w.bad_after_mark is None
                ]
                bad_before_mark = [w for w in self.slo_watchers if w.bad_before_mark]
                slo_text_components = []
                if len(unknown) > 0:
                    slo_text_components.extend(
                        [
                            Emoji(':thinking_face:'),
                            f'{len(unknown)} SLOs are missing data:\n',
                        ],
                    )
                    for slo_watcher in unknown:
                        slo_text_components.append(f'{slo_watcher.label}\n')

                if len(bad_before_mark) > 0:
                    slo_text_components.extend(
                        [
                            Emoji(':grimacing:'),
                            f'{len(bad_before_mark)} SLOs were failing before deploy, and will be ignored:\n',
                        ],
                    )
                    for slo_watcher in bad_before_mark:
                        slo_text_components.append(f'{slo_watcher.label}\n')

                remaining = len(self.slo_watchers) - len(unknown) - len(bad_before_mark)

                if remaining == len(self.slo_watchers):
                    slo_text_components = [
                        Emoji(':ok_hand:'),
                        f'All {len(self.slo_watchers)} SLOs are currently passing.',
                    ]
                else:
                    if remaining > 0:
                        slo_text_components.append(
                            f'The remaining {remaining} SLOs are currently passing.',
                        )

            if summary:
                # For summary, only display emojis.
                if self.is_terminal_state(self.state):
                    return ''
                else:
                    return ' '.join(
                        [c for c in slo_text_components if isinstance(c, Emoji)],
                    )
            else:
                # Display all text for non-summary mode, but hide Emojis if we're in a terminal state, to prevent
                # things like :alert: from blinking until the end of time.
                if self.is_terminal_state(self.state):
                    return ' '.join(
                        [c for c in slo_text_components if not isinstance(c, Emoji)],
                    )
                else:
                    return ' '.join(slo_text_components)
        else:
            return ''

    def start_slo_watcher_threads(self, service: str, soa_dir: str) -> None:
        _, self.slo_watchers = watch_slos_for_service(
            service=service,
            individual_slo_callback=self.individual_slo_callback,
            all_slos_callback=self.all_slos_callback,
            sfx_api_token=self.get_signalfx_api_token(),
            soa_dir=soa_dir,
        )

    @abc.abstractmethod
    def get_signalfx_api_token(self) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def auto_rollbacks_enabled(self) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_auto_rollback_delay(self) -> float:
        raise NotImplementedError()

    def start_alertmanager_watcher_threads(
        self,
        alertmanager_url: str,
        filters: List[List[str]],
        check_interval_s: int = _DEFAULT_CHECK_INTERVAL_S,
    ) -> None:
        _, self.alertmanager_watcher = watch_alertmanager_alerts(
            alertmanager_url=alertmanager_url,
            filters=filters,
            individual_alert_callback=self.individual_alertmanager_callback,
            all_alert_callback=self.all_alertmanager_callback,
            check_interval_s=check_interval_s,
        )

    def any_slo_failing(self) -> bool:
        return self.auto_rollbacks_enabled() and self.slo_watchers is not None and any(
            w.failing for w in self.slo_watchers
        )

    def any_alertmanager_failing(self) -> bool:
        return (
            self.auto_rollbacks_enabled()
            and self.alertmanager_watcher is not None
            and len(self.alertmanager_watcher.active_alerts) > 0
        )

    def any_rollback_condition_failing(self) -> bool:
        return self.any_slo_failing() or self.any_alertmanager_failing()

    def individual_slo_callback(self, label: str, bad: Optional[bool]) -> None:
        if bad:
            self.update_slack_thread(f'SLO started failing: {label}', color='danger')
        else:
            self.update_slack_thread(f'SLO is now OK: {label}', color='good')

    def all_slos_callback(self, bad: bool) -> None:
        if bad:
            self.trigger('slos_started_failing')
        else:
            self.trigger('slos_stopped_failing')
        self.update_slack()

    def individual_alertmanager_callback(self, label: str, failing: bool, dry_run: bool = False) -> None:
        prefix = '[DRY-RUN] ' if dry_run else ''
        if failing:
            self.update_slack_thread(f'{prefix}AlertManager alert started firing: {label}', color='danger')
        else:
            self.update_slack_thread(f'{prefix}AlertManager alert resolved: {label}', color='good')
        self.update_slack()

    def all_alertmanager_callback(self, failing: bool) -> None:
        if failing:
            self.trigger('alertmanager_started_failing')
        else:
            self.trigger('alertmanager_stopped_failing')
        self.update_slack()

    def get_alertmanager_text(self, summary: bool) -> str:
        if self.alertmanager_watcher is not None:
            all_active = self.alertmanager_watcher.active_alerts
            if all_active:
                components = [
                    Emoji(':alert:'),
                    f'{len(all_active)} AlertManager alert(s) firing:\n',
                ]
                for alert_name in sorted(all_active):
                    components.append(f'{alert_name}\n')
            else:
                components = [
                    Emoji(':ok_hand:'),
                    'No AlertManager alerts firing.',
                ]
            if summary:
                if self.is_terminal_state(self.state):
                    return ''
                return ' '.join([c for c in components if isinstance(c, Emoji)])
            else:
                if self.is_terminal_state(self.state):
                    return ' '.join([c for c in components if not isinstance(c, Emoji)])
                return ' '.join(components)
        return ''

    def start_auto_rollback_countdown(self, trigger: str, extra_text: str) -> None:
        self.start_timer(
            timeout=self.get_auto_rollback_delay(),
            trigger=trigger,
            message_verb='automatically roll back',
            extra_text=extra_text,
        )

    def cancel_auto_rollback_countdown(self, trigger: str) -> None:
        self.cancel_timer(trigger=trigger)
