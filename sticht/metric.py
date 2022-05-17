# Copyright 2019-2022 Yelp Inc.
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

    @abc.abstractmethod
    def process_datapoint(self, props, datapoint, timestamp) -> None:
        raise NotImplementedError()


def watch_metrics_for_service() -> Tuple[List[threading.Thread], List[MetricWatcher]]:
    return ()


class MetricSlackDeploymentProcess(SlackDeploymentProcess, abc.ABC):
    auto_rollback_delay: float

    @abc.abstractmethod
    def start_metric_watcher_threads(self, service: str, soa_dir: str) -> None:
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

    
