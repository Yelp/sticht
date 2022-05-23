# Copyright 2019 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import abc
import functools
import os
import textwrap
import threading
import time
import traceback
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import List
from typing import Optional
from typing import Tuple

import splunklib.client
import splunklib.results

from sticht.types import Emoji
import pytimeparse

try:
    from slo_utils.yelpsoa_configs import get_slo_files_from_soaconfigs
    from slo_transcoder.composite_sinks.signalform_detectors import (
        make_signalform_detector_composite_sink,
    )
    from slo_transcoder.sources.yelpsoaconfigs import YelpSoaConfigsSource

    SLO_TRANSCODER_LOADED = True
except ImportError:
    SLO_TRANSCODER_LOADED = False

import tempfile

from sticht.signalfx import tail_signalfx
from sticht.slack import SlackDeploymentProcess

def get_relevant_slo_files(service, soa_dir):
    return [
        f
        for f in get_slo_files_from_soaconfigs(soa_dir)
        if f.startswith(os.path.join(soa_dir, service, ''))
    ]


def get_slos_for_service(service, soa_dir) -> Generator:
    if not SLO_TRANSCODER_LOADED:
        return

    slo_files = get_relevant_slo_files(service, soa_dir)
    with tempfile.TemporaryDirectory() as temp_dir:
        SFDCS = make_signalform_detector_composite_sink(output_directory=temp_dir)
        sources = [YelpSoaConfigsSource(file) for file in slo_files]
        if not sources:
            return  # SFDCS instantiation crashes with IndexError if sources is an empty list.
        composite_sink = SFDCS(service=service, sources=sources)

        # The sinks that we get from composite_sink.sinks() have a `source` attribute, but it's a different type than
        # what we have in sources, and don't have alert_config. We build a map here so we can easily look up the
        # alert_config for each sink.
        alert_config_by_ts_metric = {
            (slo_group.timeseries, slo.metric): slo_group.alert_config
            for source in sources
            for slo_group in source.slo_groups
            for slo in slo_group.slos
        }

        for sink in composite_sink.sinks():
            if not alert_config_by_ts_metric[
                sink.source.timeseries, sink.source.metric
            ].is_silent:
                signalflow, rules = sink.generate_signalflow_signals_and_rules()
                query = textwrap.dedent('\n'.join(signalflow))
                yield sink, query


class SLODemultiplexer:
    def __init__(
        self,
        sink: Any,
        individual_slo_callback: Callable[['SLOWatcher'], None],
        start_timestamp: Optional[float] = None,
        max_duration: float = 3600,
    ) -> None:
        self.sink = sink
        self.individual_slo_callback = individual_slo_callback
        self.max_duration = max_duration
        if start_timestamp is None:
            self.start_timestamp = time.time()
        else:
            self.start_timestamp = start_timestamp

        self.slo_watchers_by_label: Dict[str, 'SLOWatcher'] = {}
        for slo in sink.source.slos:
            label = sink._get_detector_label(slo)
            watcher = SLOWatcher(
                slo,
                individual_slo_callback,
                self.start_timestamp,
                label,
                max_duration=max_duration,
            )
            self.slo_watchers_by_label[label] = watcher

    def process_datapoint(self, props, datapoint, timestamp) -> None:
        slo_label = props['dimensions']['sf_metric'].rsplit('.', 1)[0]
        if not slo_label.startswith('_SF_COMP_'):
            watcher = self.slo_watchers_by_label[slo_label]
            watcher.process_datapoint(props, datapoint, timestamp)

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


class SplunkMetricWatcher(MetricWatcher):
    def __init__(
        self,
        label: str,
        query: str,
        on_failure_callback: Callable[['MetricWatcher'], None],
        splunk_host: str,
        splunk_port: int,
        credentials_callback: Callable[[], Tuple[str, str]]
    ) -> None:
        super().__init__(label, on_failure_callback)
        self._query = query
        # TODO: should we share a global version of this so that we're
        # not logging in a million times?
        self._splunk: Optional[splunklib.client.Service] = None
        self._credentials_callback = credentials_callback
        self._splunk_host = splunk_host
        self._splunk_port = splunk_port

    def _splunk_login(self) -> None:
        user, password = self._credentials_callback()
        self.splunk = splunklib.client.connect(
            host=self._splunk_host,
            port=self._splunk_port,
            username=user,
            password=password,
        )

    # TODO: need to figure out what to do re: min. frequency here
    # since splunk searches can take a while
    # TODO: what if a query takes longer than the min. frequency?
    # do we just cut it off?
    def query(self) -> None:
        if not self._splunk:
            self._splunk_login()
            assert self._splunk

        # TODO: do we need set set any other kwargs? e.g., earliest_time or output mode?
        job = self._splunk.search(query=self._query)

        # TODO: how long do we actually want to wait?
        result_total_wait_time_s = 0
        # TODO: should this be hardcoded? dynamic? come from user config?
        result_poll_time_s = 1
        while not job.is_done():
            time.sleep(secs=result_poll_time_s)
            result_total_wait_time_s += result_poll_time_s

        result_reader = splunklib.results.JSONResultsReader(
            stream=job.results(
                output_mode='json',
            ),
        )

        for result in result_reader:
            if isinstance(result, splunklib.results.Message):
                # Diagnostic messages may be returned in the results
                logger.debug(f"[splunk] {result.type}: {result.message}")
            elif isinstance(result, dict):
                # Normal events are returned as dicts
                print result
        assert result_reader.is_preview == False



class SLOWatcher:
    def __init__(
        self,
        slo: Any,
        callback: Callable[['SLOWatcher'], Any],
        start_timestamp: float,
        label: str,
        max_duration: float,
    ) -> None:
        self.slo = slo
        self.window: List[Tuple[float, float]] = []
        self.max_duration = max_duration
        self.failing: Optional[bool] = None
        self.bad_after_mark: Optional[bool] = None
        self.bad_before_mark: Optional[bool] = None
        self.callback = callback
        self.start_timestamp = start_timestamp
        self.label = label

    def process_datapoint(self, props, datapoint, timestamp) -> None:
        self.window.append((timestamp, datapoint))
        self.trim_window()

        if timestamp > self.start_timestamp:
            self.bad_after_mark = self.is_window_bad()
        else:
            self.bad_before_mark = self.is_window_bad()

        old_failing = self.failing
        self.failing = self.bad_after_mark and not self.bad_before_mark

        if self.failing == (not old_failing):
            self.callback(self)

    def trim_window(self) -> None:
        old_window = self.window
        min_ts = max(ts for ts, d in old_window) - self.window_duration()
        new_window = [(ts, d) for (ts, d) in old_window if ts > min_ts]
        self.window = new_window

    def window_duration(self) -> float:
        """Many of our SLOs are defined with durations of 1 hour or more; this is great if you're trying to avoid being
        paged, but not helpful for a deployment that you expect to finish within a few minutes. self.max_duration
        allows us to cap the length of time that we consider. This should make us a bit more sensitive."""
        return min(self.max_duration, pytimeparse.parse(str(self.slo.config.duration)))

    def is_window_bad(self) -> bool:
        bad_datapoints = len(
            [1 for ts, d in self.window if d > self.slo.config.threshold],
        )
        return (
            bad_datapoints / len(self.window)
        ) >= 1.0 - self.slo.config.percent_of_duration / 100.0


def print_exceptions_wrapper(fn):
    def inner(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            traceback.print_exc()
            raise

    return inner

def watch_metrics_for_service(service: str, soa_dir: str) -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    threads: List[threading.Thread] = []
    watchers: List[MetricWatcher] = []

    return threads, watchers


def watch_slos_for_service(
    service: str,
    individual_slo_callback: Callable[[str, Optional[bool]], Any],
    all_slos_callback: Callable[[bool], Any],
    sfx_api_token: str,
    soa_dir: str,
    start_timestamp: Optional[float] = None,
) -> Tuple[List[threading.Thread], List[SLOWatcher]]:
    threads = []
    watchers: List[SLOWatcher] = []

    failing = False

    def callback_wrapper(watcher: 'SLOWatcher') -> None:
        nonlocal failing
        old_failing = failing
        new_failing = any(w.failing for w in watchers)
        individual_slo_callback(watcher.label, watcher.failing)

        failing = new_failing

        if new_failing == (not old_failing):
            all_slos_callback(new_failing)

    for sink, query in get_slos_for_service(service, soa_dir):
        demux = SLODemultiplexer(
            sink,
            individual_slo_callback=callback_wrapper,
            start_timestamp=start_timestamp,
        )
        thread = threading.Thread(
            target=print_exceptions_wrapper(
                functools.partial(
                    tail_signalfx,
                    query,
                    lookback_seconds=30,
                    callback=print_exceptions_wrapper(demux.process_datapoint),
                    sfx_api_token=sfx_api_token,
                ),
            ),
            daemon=True,
        )
        threads.append(thread)
        thread.start()
        watchers.extend(demux.slo_watchers_by_label.values())

    return threads, watchers


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
