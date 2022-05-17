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
import threading
import traceback
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

import pytimeparse

from sticht.slack import SlackDeploymentProcess


# TODO: Rethink if MetricDemultiplexer is required


class MetricWatcher:
    def __init__(
        self,
        metric: Any,
        callback: Callable[['MetricWatcher'], Any],
        start_timestamp: float,
        label: str,
        max_duration: float,
    ) -> None:
        self.metric = metric
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
        return min(self.max_duration, pytimeparse.parse(str(self.metric.config.duration)))

    def is_window_bad(self) -> bool:
        bad_datapoints = len(
            [1 for ts, d in self.window if d > self.metric.config.threshold],
        )
        return (
            bad_datapoints / len(self.window)
        ) >= 1.0 - self.metric.config.percent_of_duration / 100.0


def print_exceptions_wrapper(fn):
    def inner(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            traceback.print_exc()
            raise

    return inner


def watch_metrics_for_service() -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    return ()


class MetricSlackDeploymentProcess(SlackDeploymentProcess, abc.ABC):
    auto_rollback_delay: float

    @abc.abstractmethod
    def start_metric_watcher_threads(self, service: str, soa_dir: str) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_signalfx_api_token(self) -> str:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def get_metric_api_token(self) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def auto_rollbacks_enabled(self) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_auto_rollback_delay(self) -> float:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def start_auto_rollback_countdown(self, extra_text) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def cancel_auto_rollback_countdown(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def any_metric_failing(self) -> bool:
        raise NotImplementedError()

    def individual_metric_callback(self, label: str, bad: Optional[bool]) -> None:
        if bad:
            self.update_slack_thread(f'Metric started failing: {label}', color='danger')
        else:
            self.update_slack_thread(f'Metric is now OK: {label}', color='good')

    def all_metrics_callback(self, bad: bool) -> None:
        if bad:
            self.trigger('metrics_started_failing')
        else:
            self.trigger('metrics_stopped_failing')
        self.update_slack()

    
