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
import asyncio
import datetime
import logging
import threading
import time
from typing import Callable
from typing import Collection
from typing import Dict
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple
from typing import TYPE_CHECKING
from typing import Union

import transitions.extensions
from mypy_extensions import TypedDict

log = logging.getLogger(__name__)


class TransitionDefinitionBase(TypedDict):
    """The required fields for TransitionDefinition; see
    https://mypy.readthedocs.io/en/latest/more_types.html#mixing-required-and-non-required-items"""

    trigger: str
    source: Union[str, List[str], Tuple[str, ...]]
    dest: Union[str, List[str], Tuple[str, ...]]


class TransitionDefinition(TransitionDefinitionBase, total=False):
    unless: List[Union[str, Callable]]
    conditions: List[Union[str, Callable]]
    before: Callable


class Timer:
    """Data about one countdown started by DeploymentProcess.start_timer() (e.g., "auto-certify in 600s").

    DeploymentProcess stores these in self.timers, keyed by the trigger to run when the countdown ends, and does the
    actual scheduling and triggering itself.

    NOTE: Fields like message_verb and extra_text exist so that we can have meaningful Slack messages (including
    after restart_timer() recreates the countdown).
    """

    def __init__(self, timeout: float, message_verb: str, extra_text: str) -> None:
        self.timeout = timeout
        self.message_verb = message_verb
        self.extra_text = extra_text
        # set once the countdown has been scheduled on the event loop
        self.handle: Optional[asyncio.TimerHandle] = None


class DeploymentProcess(abc.ABC):
    if TYPE_CHECKING:
        # These attributes need to be defined in this `if TYPE_CHECKING` block, because if they exist at runtime then
        # transitions will refuse to overwrite them.
        state: str

        def trigger(self, *args, **kwargs):
            ...

    run_timeout: Optional[
        float
    ] = None  # in normal operation, this will be None, but this lets tests set a max time.

    def __init__(self) -> None:
        self.event_loop = asyncio.get_event_loop()
        self.finished_event = asyncio.Event()
        # running timers, keyed by the trigger they'll run: timers are started and cancelled from several threads
        # (watchers, Slack button handlers, the event loop itself), so all access goes through timers_lock
        self.timers: Dict[str, Timer] = {}
        self.timers_lock = threading.Lock()

        self.machine = transitions.extensions.LockedMachine(
            model=self,
            states=list(self.states()),
            transitions=list(self.valid_transitions()),
            initial=self.start_state(),
            after_state_change=self.after_state_change,
            before_state_change=self.before_state_change,
            queued=True,
        )

    @abc.abstractmethod
    def status_code_by_state(self) -> Mapping[str, int]:
        raise NotImplementedError()

    @abc.abstractmethod
    def states(self) -> Collection['str']:
        raise NotImplementedError()

    @abc.abstractmethod
    def valid_transitions(self) -> Iterator[TransitionDefinition]:
        raise NotImplementedError()

    @abc.abstractmethod
    def start_transition(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def start_state(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def notify_users(self, message: str) -> None:
        """Print a log line somewhere that users can see it, e.g. Slack."""
        raise NotImplementedError()

    def is_terminal_state(self, state: str) -> bool:
        return state in self.status_code_by_state()

    def is_finished(self) -> bool:
        return self.finished_event.is_set()

    def finish(self):
        self.finished_event.set()

    def run(self):
        return self.event_loop.run_until_complete(self.run_async())

    async def run_async(self) -> int:
        self.trigger(self.start_transition())
        await asyncio.wait_for(self.finished_event.wait(), timeout=self.run_timeout)
        return self.status_code_by_state().get(self.state, 3)

    def after_state_change(self):
        if self.state in self.status_code_by_state():
            self.event_loop.call_soon_threadsafe(self.finished_event.set)

    def start_timer(self, timeout: float, trigger: str, message_verb: str, extra_text: str = '') -> None:
        """Start a countdown that runs `trigger` after `timeout` seconds.

        Timers are tracked per trigger: this replaces any running timer with the same trigger, but leaves timers with
        other triggers alone."""
        self.cancel_timer(trigger=trigger)
        timer = Timer(timeout=timeout, message_verb=message_verb, extra_text=extra_text)
        timer_start = time.time()
        timer_end = timer_start + timeout
        formatted_time = datetime.datetime.fromtimestamp(timer_end).strftime('%X')
        self.notify_users(
            f'Will {message_verb} in {timeout} seconds, (at {formatted_time})! {extra_text}',
        )

        def times_up():
            with self.timers_lock:
                if self.timers.get(trigger) is not timer:
                    # this timer was cancelled or replaced after it was scheduled
                    return
                del self.timers[trigger]
            self.notify_users(f"Time's up, will now {message_verb}.")
            self.trigger(trigger)

        def schedule_callback():
            """Unfortunately, call_at is not threadsafe, and there's no call_at_threadsafe, so we need to schedule the
            call to call_at with call_soon_threadsafe."""
            with self.timers_lock:
                timer.handle = self.event_loop.call_later(timeout, times_up)
                self.timers[trigger] = timer

        self.event_loop.call_soon_threadsafe(schedule_callback)

    def cancel_timer(self, trigger: Optional[str] = None) -> bool:
        """Cancel the running timer for `trigger`, or every running timer if no trigger is given.

        Returns whether any timer was cancelled."""
        with self.timers_lock:
            if trigger is None:
                cancelled = list(self.timers.values())
                self.timers.clear()
            else:
                timer = self.timers.pop(trigger, None)
                cancelled = [] if timer is None else [timer]
            for timer in cancelled:
                if timer.handle is not None:
                    timer.handle.cancel()

        for timer in cancelled:
            self.notify_users(f'Countdown to {timer.message_verb} cancelled.')
        return len(cancelled) > 0

    def restart_timer(self) -> None:
        """Restart every running timer from the beginning of its countdown."""
        with self.timers_lock:
            running = list(self.timers.items())
        for trigger, timer in running:
            self.start_timer(
                timeout=timer.timeout,
                trigger=trigger,
                message_verb=timer.message_verb,
                extra_text=timer.extra_text,
            )

    def is_timer_running(self, trigger: Optional[str] = None) -> bool:
        """Whether the timer for `trigger` is running, or whether any timer is running if no trigger is given."""
        with self.timers_lock:
            if trigger is None:
                return len(self.timers) > 0
            return trigger in self.timers

    def before_state_change(self):
        pass
