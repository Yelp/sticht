import abc
from typing import Optional, List
from sticht.rollbacks.slo import SLOWatcher
from sticht.rollbacks.metrics import MetricWatcher
from sticht.slack import SlackDeploymentProcess
from sticht.types import Emoji
from enum import Enum
from enum import auto

class RollbackResultMode(Enum):
    RESULTS = auto()
    VALUE = auto()

class RollbackSlackDeploymentProcess(SlackDeploymentProcess, abc.ABC):
    slo_watchers: Optional[List[SLOWatcher]] = None
    metric_watchers: Optional[List[MetricWatcher]] = None

    def get_extra_blocks_for_deployment(self):
        blocks = []
        slo_text = self.get_slo_text(summary=False)
        if slo_text:
            blocks.append(
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': slo_text}},
            )
        return blocks

    def get_extra_summary_parts_for_deployment(self) -> List[str]:
        parts = super().get_extra_summary_parts_for_deployment()
        slo_text = self.get_slo_text(summary=True)
        if slo_text:
            parts.append(slo_text)

        metric_text = self.get_metric_text(summary=True)
        if metric_text:
            parts.append(metric_text)

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


    def get_metric_text(self, summary: bool) -> str:
        metric_text_components = []
        if self.metric_watchers is not None and len(self.metric_watchers) > 0:
            failing = [w for w in self.metric_watchers if w.failing]

            if failing:
                metric_text_components = [
                    Emoji(':alert:'),
                    f'{len(failing)} of {len(self.metric_watchers)} rollback conditions are failing:\n',
                ]
                for _ in failing:
                    # TODO: figure out how to label these for presentation in slack
                    pass

                # TODO: add text about going over the allowed failing conditions and automatically rolling back
                # TODO: add text about ignoring rules that were failing pre-deploy
                # TODO: add text about # of conditions with no data
            else:
                metric_text_components = [
                    Emoji(':ok_hand:'),
                    f'All {len(self.metric_watchers)} rollback conditions are currently passing.',
                ]

            if summary:
                # For summary, only display emojis.
                if self.is_terminal_state(self.state):
                    return ''
                else:
                    return ' '.join(
                        [c for c in metric_text_components if isinstance(c, Emoji)],
                    )
            else:
                # Display all text for non-summary mode, but hide Emojis if we're in a terminal state, to prevent
                # things like :alert: from blinking until the end of time.
                if self.is_terminal_state(self.state):
                    return ' '.join(
                        [c for c in metric_text_components if not isinstance(c, Emoji)],
                    )
                else:
                    return ' '.join(metric_text_components)
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


    def start_metric_watcher_threads(self, service: str, soa_dir: str) -> None:
        _, self.metric_watchers = watch_metrics_for_service(
            service=service,
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

    def any_slo_failing(self) -> bool:
        return self.auto_rollbacks_enabled() and self.slo_watchers is not None and any(
            w.failing for w in self.slo_watchers
        )

    def any_metric_failing(self) -> bool:
        return self.auto_rollbacks_enabled() and self.metric_watchers is not None and any(
            w.failing for w in self.metric_watchers
        )

    def any_rollback_condition_failing(self) -> bool:
        return self.any_slo_failing() or self.any_metric_failing()

    # TODO: figure out what to do here - duplicate or make slo vs metric a param
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

    def start_auto_rollback_countdown(self, extra_text) -> None:
        self.start_timer(
            self.get_auto_rollback_delay(),
            'rollback_slo_failure',
            'automatically roll back',
            extra_text=extra_text,
        )

    def cancel_auto_rollback_countdown(self) -> None:
        self.cancel_timer('rollback_slo_failure')
