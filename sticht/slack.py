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
import json
import logging
import traceback
from typing import List
from typing import Optional

import requests
import transitions
from slackclient import SlackClient
from typing_extensions import TypedDict

from sticht.state_machine import DeploymentProcess


try:
    from scribereader import scribereader
    from clog.readers import construct_conn_msg
    from clog.readers import find_tail_host
except ImportError:
    scribereader = None
    construct_conn_msg = None
    find_tail_host = None

SLACK_WEBHOOK_STREAM = 'stream_slack_incoming_webhook'
SCRIBE_ENV = {
    'region': 'uswest2-prod',
    'superregion': None,
    'ecosystem': None,
}
log = logging.getLogger(__name__)


def log_error(msg):
    # Jenkins logs are full of "ratelimited" errors from sticht. They aren't really
    # interesting anyway and we just ignore them, so let's blacklist them.
    if 'ratelimited' not in msg:
        log.error(msg)


class Emoji(str):
    """
    Wrap emojis in this subclass so we can select only the emojis or only the detail sections
    when displaying messages in Slack.
    """
    pass


class ButtonPress:
    def __init__(self, event):
        self.event = event
        self.username = event['user']['username']
        self.response_url = event['response_url']
        # TODO: Handle multiple actions?
        self.action = event['actions'][0]['value']
        self.thread_ts = event['container'].get('thread_ts', None)
        self.channel = event['channel']['name']

    def __repr__(self):
        return self.event

    def update(self, blocks):
        # Implements responding to button presses
        # https://api.slack.com/messaging/interactivity/enabling#responding-to-interactions
        # But isn't the api_call method per-se
        # https://github.com/slackapi/python-slackclient/issues/270
        requests.post(self.response_url, json={'blocks': truncate_blocks_text(blocks)})


def event_to_buttonpress(event):
    return ButtonPress(event=event)


def parse_webhook_event_json(line):
    event = json.loads(line)
    log.debug(event)
    return event


def is_relevant_event(event):
    """
    Filter useful events from the slack webhook stream.

    The event stream might contain mixed Slack API data (Events and Blocks)
    see more https://api.slack.com/events-api
    """
    if event and 'type' in event:
        if event['type'] == 'block_actions':
            return True
    return False


async def get_slack_events():
    if scribereader is None:
        logging.error('Scribereader unavailable. Not tailing slack events.')
        return

    # get_tail_host_and_port returns a fake hostname for some reason, which needs to be passed to find_tail_host to get
    # an actual hostname.
    not_a_real_host, port = scribereader.get_tail_host_and_port(**SCRIBE_ENV)
    host = find_tail_host(not_a_real_host)

    while True:
        reader, writer = await asyncio.open_connection(host=host, port=port)
        writer.write(construct_conn_msg(stream=SLACK_WEBHOOK_STREAM).encode('utf-8'))
        await writer.drain()

        while True:
            line = await reader.readline()
            log.debug(f"got log line {line.decode('utf-8')}")
            if not line:
                break
            event = parse_webhook_event_json(line)
            if is_relevant_event(event):
                yield event

        log.warning('Lost connection to scribe host; reconnecting')


class SlackBlockText(TypedDict, total=False):
    type: str
    text: str
    emoji: bool


class SlackConfirmation(TypedDict, total=False):
    title: SlackBlockText
    text: SlackBlockText
    confirm: SlackBlockText
    deny: SlackBlockText


class SlackBlockElement(TypedDict, total=False):
    type: str
    value: str
    text: SlackBlockText
    confirm: SlackConfirmation


class SlackBlock(TypedDict, total=False):
    text: SlackBlockText
    type: str
    block_id: str
    elements: SlackBlockElement


class SlackDeploymentProcess(DeploymentProcess, abc.ABC):
    default_slack_channel: Optional[str] = None

    def __init__(self) -> None:
        super().__init__()
        self.human_readable_status = 'Initializing...'
        self.slack_client = self.get_slack_client()
        self.last_action = None
        self.summary_blocks_str = ''
        self.detail_blocks_str = ''
        self.slack_channel = self.get_slack_channel()
        self.send_initial_slack_message()

        asyncio.ensure_future(self.listen_for_slack_events(), loop=self.event_loop)
        asyncio.ensure_future(self.periodically_update_slack(), loop=self.event_loop)

    @abc.abstractmethod
    def get_slack_client(self) -> SlackClient:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_slack_channel(self) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_deployment_name(self) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_progress(self, summary=False) -> str:
        raise NotImplementedError()

    def get_active_button(self) -> Optional[str]:
        return None

    @abc.abstractmethod
    def get_button_text(self, button, is_active) -> str:
        raise NotImplementedError()

    def get_button_element(self, button, is_active) -> SlackBlockElement:
        element: SlackBlockElement = {
            'type': 'button',
            'text': {
                'type': 'plain_text',
                'text': self.get_button_text(button, is_active),
                'emoji': True,
            },
            'value': button,
        }
        if not is_active:
            element['confirm'] = self.get_confirmation_object(button)
        return element

    def get_summary_blocks_for_deployment(self) -> List[SlackBlock]:
        deployment_name = self.get_deployment_name()
        progress = self.get_progress(summary=True)
        button_elements = self.get_button_elements()

        summary_parts = [deployment_name, progress]
        summary_parts.extend(self.get_extra_summary_parts_for_deployment())

        blocks: List[SlackBlock] = [
            {
                'type': 'section',
                'text': {'type': 'mrkdwn', 'text': ' | '.join(summary_parts)},
            },
        ]
        if button_elements != []:
            blocks.append(
                {
                    'type': 'actions',
                    'block_id': 'deployment_actions',
                    'elements': button_elements,
                },
            )

        return blocks

    def get_detail_slack_blocks_for_deployment(self) -> List[SlackBlock]:
        status = getattr(self, 'state', None) or 'Uninitialized'
        deployment_name = self.get_deployment_name()
        message = self.human_readable_status
        progress = self.get_progress()
        last_action = self.last_action

        blocks: List[SlackBlock] = [
            {
                'type': 'section',
                'text': {'type': 'mrkdwn', 'text': f'{deployment_name}'},
            },
            {'type': 'section', 'text': {'type': 'mrkdwn', 'text': message}},
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': f'State machine: `{status}`\nProgress: {progress}\nLast operator action: {last_action}',
                },
            },
        ]

        blocks.extend(self.get_extra_blocks_for_deployment())
        return blocks

    def get_extra_blocks_for_deployment(self) -> List[SlackBlock]:
        return []

    def get_extra_summary_parts_for_deployment(self) -> List[str]:
        return []

    def get_button_elements(self):
        elements = []
        active_button = self.get_active_button()
        for button in self.get_available_buttons():
            is_active = button == active_button
            elements.append(self.get_button_element(button=button, is_active=is_active))
        return elements

    def get_confirmation_object(self, action) -> SlackConfirmation:
        return {
            'title': {'type': 'plain_text', 'text': 'Are you sure?'},
            'text': {'type': 'mrkdwn', 'text': f'Did you mean to press {action}?'},
            'confirm': {'type': 'plain_text', 'text': 'Yes. Do it!'},
            'deny': {'type': 'plain_text', 'text': "Stop, I've changed my mind!"},
        }

    def get_available_buttons(self) -> List[str]:
        buttons = []

        if self.is_terminal_state(self.state):
            # If we're about to exit, always clear the buttons, since once we exit the buttons will stop working.
            return []

        for trigger in self.machine.get_triggers(self.state):
            suffix = '_button_clicked'
            if trigger.endswith(suffix):
                if all(
                    cond.target ==
                    self.machine.resolve_callable(
                        cond.func,
                        transitions.EventData(
                            self.state, self, self.machine, self, args=(), kwargs={},
                        ),
                    )()
                    for transition in self.machine.get_transitions(
                        source=self.state, trigger=trigger,
                    )
                    for cond in transition.conditions
                ):
                    buttons.append(trigger[: -len(suffix)])

        return buttons

    def slack_api_call(self, *args, **kwargs):
        """Makes an api call to Slack via a client, if it exists. Non-Slack errors
        such as JSONDecodeError are caught and returned.
        """
        if self.slack_client is None:
            return {'ok': False, 'error': 'Slack client does not exist'}
        else:
            try:
                resp = self.slack_client.api_call(*args, **kwargs)
                return resp
            except Exception as e:
                # leaving error/warning logging to callers, only debug log here.
                log.debug(f'Exception encountered when making Slack api call: {e}')
                return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def update_slack_thread(self, message, color=None):
        message = message[:3000]
        if self.slack_client is None:
            print(f'Would update the slack thread with: {message}', flush=True)
            return
        else:
            print(f'Updating slack thread with: {message}', flush=True)
        if color:
            resp = self.slack_api_call(
                'chat.postMessage',
                channel=self.slack_channel,
                attachments=[{'text': message, 'color': color}],
                thread_ts=self.slack_ts,
            )
        else:
            resp = self.slack_api_call(
                'chat.postMessage',
                channel=self.slack_channel,
                text=message,
                thread_ts=self.slack_ts,
            )

        if resp['ok'] is not True:
            log_error(f"Posting to slack failed: {resp['error']}")

    def send_initial_slack_message(self):
        if self.slack_client is None:
            return
        summary_blocks = self.get_summary_blocks_for_deployment()
        detail_blocks = self.get_detail_slack_blocks_for_deployment()
        resp = self.slack_api_call(
            'chat.postMessage', blocks=truncate_blocks_text(summary_blocks), channel=self.slack_channel,
        )
        self.slack_ts = resp['message']['ts'] if resp and resp['ok'] else None

        self.slack_channel_id = resp.get('channel')
        if not self.slack_channel_id:
            log.warning(
                f"Is '{self.slack_channel}' a valid channel name? No channel ID in response",
            )
            if (
                self.default_slack_channel
                and self.slack_channel != self.default_slack_channel
            ):
                log.warning(
                    f'Falling back to default channel {self.default_slack_channel}',
                )
                self.slack_channel = self.default_slack_channel
                self.send_initial_slack_message()
                return
            else:
                log_error('Continuing without Slack')
                self.slack_client = None
                return

        if resp['ok'] is not True:
            log_error(f"Posting to slack failed: {resp['error']}")

        resp = self.slack_api_call(
            'chat.postMessage',
            blocks=truncate_blocks_text(detail_blocks),
            channel=self.slack_channel,
            thread_ts=self.slack_ts,
        )
        self.detail_slack_ts = resp['message']['ts'] if resp and resp['ok'] else None

        if resp['ok'] is not True:
            log_error(f"Posting detail to slack failed: {resp['error']}")
            if resp['error'] == 'invalid_blocks':
                log_error(f'Blocks: {detail_blocks!r}')

    def update_slack(self):
        if self.slack_client is None:
            return
        summary_blocks = self.get_summary_blocks_for_deployment()
        detail_blocks = self.get_detail_slack_blocks_for_deployment()

        summary_blocks_str = json.dumps(summary_blocks, sort_keys=True)
        detail_blocks_str = json.dumps(detail_blocks, sort_keys=True)

        if self.summary_blocks_str != summary_blocks_str:
            resp = self.slack_api_call(
                'chat.update',
                channel=self.slack_channel_id,
                blocks=truncate_blocks_text(summary_blocks),
                ts=self.slack_ts,
            )
            if resp['ok']:
                self.old_summary_blocks_str = summary_blocks_str
            else:
                self.old_summary_blocks_str = ''  # So we retry next time.
                log_error(f"Posting to slack failed: {resp['error']}")

        if self.detail_blocks_str != detail_blocks_str:
            resp = self.slack_api_call(
                'chat.update',
                channel=self.slack_channel_id,
                blocks=truncate_blocks_text(detail_blocks),
                ts=self.detail_slack_ts,
            )
            if resp['ok']:
                self.old_detail_blocks_str = detail_blocks_str
            else:
                self.old_detail_blocks_str = ''  # So we retry next time.
                log_error(f"Posting detail to slack failed: {resp['error']}")

    def update_slack_status(self, message):
        self.human_readable_status = message
        self.update_slack()

    async def periodically_update_slack(self):
        while self.state not in self.status_code_by_state():
            self.update_slack()
            await asyncio.sleep(20)

    def is_relevant_buttonpress(self, buttonpress):
        return self.slack_ts == buttonpress.thread_ts

    async def listen_for_slack_events(self):
        log.debug('Listening for slack events...')
        try:
            async for event in get_slack_events():
                try:
                    log.debug(f'Got slack event: {event}')
                    buttonpress = event_to_buttonpress(event)
                    if self.is_relevant_buttonpress(buttonpress):
                        self.update_slack_thread(
                            f'<@{buttonpress.username}> pressed {buttonpress.action}',
                        )
                        self.last_action = buttonpress.action

                        try:
                            self.trigger(f'{buttonpress.action}_button_clicked')
                        except (transitions.core.MachineError, AttributeError):
                            self.update_slack_thread(f'Error: {traceback.format_exc()}')
                    else:
                        log.debug(
                            'But it was not relevant to this instance of mark-for-deployment',
                        )
                except Exception:
                    log_error(f'Exception while processing event: {traceback.format_exc()}')
                    log.debug(f'event: {event!r}')
        except Exception:
            log_error('\n'.join(
                'Uncaught error in listen_for_slack_events:',
                traceback.format_exc(),
                'Restarting event listener.',
            ))
            await self.listen_for_slack_events()

    def notify_users(self, message):
        self.update_slack_thread(message)


def truncate_blocks_text(blocks: List[SlackBlock]) -> List[SlackBlock]:
    """Modifies blocks to restrict all text to 3000 characters."""
    for block in blocks:
        try:
            block['text']['text'] = block['text']['text'][:3000]
        except KeyError:
            pass

    return blocks
