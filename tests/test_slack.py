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
from unittest import mock

from slackclient import SlackClient

from sticht import slack

REAL_ROLLBACK_PRESS = {
    'type': 'block_actions',
    'team': {'id': 'T0289TLJY', 'domain': 'yelp'},
    'user': {'id': 'UA6JBNA0Z', 'username': 'kwa', 'team_id': 'T0289TLJY'},
    'api_app_id': 'AAJ7PL9ST',
    'token': 'WYj864vvUYGtcV4pyfmO4rOQ',
    'container': {
        'type': 'message',
        'message_ts': '1551306063.241500',
        'channel_id': 'CA05GTDB9',
        'is_ephemeral': False,
        'thread_ts': '1551306063.241500',
    },
    'trigger_id': '562162233904.2281938644.c15af7fa5b7e10836c6db7ece2f53eab',
    'channel': {'id': 'CA05GTDB9', 'name': 'paasta'},
    'message': {
        'type': 'message',
        'subtype': 'bot_message',
        'text': "This content can't be displayed.",
        'ts': '1551306063.241500',
        'username': 'PaaSTA',
        'bot_id': 'BAJ8JMV9V',
        'thread_ts': '1551306063.241500',
        'reply_count': 1,
        'reply_users_count': 1,
        'latest_reply': '1551306064.241600',
        'reply_users': ['BAJ8JMV9V'],
        'replies': [{'user': 'BAJ8JMV9V', 'ts': '1551306064.241600'}],
        'subscribed': False,
        'blocks': [
            {
                'type': 'section',
                'block_id': 'K15S',
                'text': {
                    'type': 'mrkdwn',
                    'text': '*compute-infra-test-service* - Marked *baaf4d7b2ddf* for '
                            'deployment on *mesosstage.everything*.\n',
                    'verbatim': False,
                },
            },
            {
                'type': 'actions',
                'block_id': 'rollback_block1',
                'elements': [
                    {
                        'type': 'button',
                        'action_id': 'cLjFK',
                        'text': {
                            'type': 'plain_text',
                            'text': 'Roll Back (Not Implemented)',
                            'emoji': True,
                        },
                        'value': 'rollback',
                    },
                    {
                        'type': 'button',
                        'action_id': 'Grjq',
                        'text': {
                            'type': 'plain_text',
                            'text': 'Continue (Not Implemented)',
                            'emoji': True,
                        },
                        'value': 'continue',
                    },
                ],
            },
        ],
    },
    'response_url': 'https://hooks.slack.com/actions/T0289TLJY/562866820372/0lRlD5JFQlLPPvqrelCpJlF9',
    'actions': [
        {
            'action_id': 'cLjFK',
            'block_id': 'rollback_block1',
            'text': {
                'type': 'plain_text',
                'text': 'Roll Back (Not Implemented)',
                'emoji': True,
            },
            'value': 'rollback',
            'type': 'button',
            'action_ts': '1551306127.199355',
        },
    ],
}  # noqa E501

MIXED_EVENTS_STREAM = [
    {
        'type': 'block_actions',
        'event_id': '123',
        'user': {
            'id': 'XXXXXXXXX',
            'username': 'XXX',
            'name': 'XXX',
            'team_id': '1234',
        },
        'token': 'xxxx',
    },
    {
        'type': 'event_callback',
        'token': 'xxxx',
        'team_id': 'xxxx',
        'event_id': '789',
        'event': {
            'type': 'link_shared',
            'user': 'ASLKDJA',
            'channel': 'AKDJL',
            'message_ts': '1597270855.028300',
            'thread_ts': '1597096154.261800',
            'event_ts': '1597270856.073628',
        },
    },
]  # noqa E501


class DummySlackDeploymentProcess(slack.SlackDeploymentProcess):
    """A minimum-viable SlackDeploymentProcess subclass."""

    def status_code_by_state(self):
        return {}

    def states(self):
        return ['_begin']

    def valid_transitions(self):
        return []

    def start_transition(self):
        raise NotImplementedError()

    def start_state(self):
        return '_begin'

    def get_slack_client(self):
        mock_client = mock.Mock(spec=SlackClient)
        mock_client.api_call.return_value = {
            'ok': True,
            'message': {'ts': 10},
            'channel': 'test',
        }
        return mock_client

    def get_slack_channel(self):
        return '#test'

    def get_deployment_name(self):
        return 'deployment name'

    def get_progress(self, summary=False):
        return 'progress%'

    def get_button_text(self, button, is_active):
        return f'{button} {is_active}'


class ErrorSlackDeploymentProcess(DummySlackDeploymentProcess):
    default_slack_channel = '#dne'

    def get_slack_client(self):
        mock_client = mock.Mock(spec=SlackClient)
        mock_client.api_call.return_value = {'ok': False, 'error': 'uh oh'}
        return mock_client


def test_slack_errors_no_exceptions():
    sdp = ErrorSlackDeploymentProcess()
    # Make sure slack methods don't fail.
    sdp.update_slack()
    sdp.update_slack_thread('Hello world')


def test_get_detail_slack_blocks_for_deployment_happy_path():

    sdp = DummySlackDeploymentProcess()
    blocks = sdp.get_detail_slack_blocks_for_deployment()
    assert blocks[0]['text']['text'] == 'deployment name'
    assert blocks[1]['text']['text'] == 'Initializing...'
    assert (
        blocks[2]['text']['text'] ==
        'State machine: `_begin`\nProgress: progress%\nLast operator action: None'
    )


def test_event_to_buttonpress_rollback():
    actual = slack.event_to_buttonpress(REAL_ROLLBACK_PRESS)
    assert actual.username == 'kwa'
    assert actual.action == 'rollback'


def test_slack_api_call():
    sdp = DummySlackDeploymentProcess()
    slack_client = sdp.slack_client

    resp = sdp.slack_api_call('some_arguments', other_arg=True)

    assert resp['ok']
    assert (
        mock.call('some_arguments', other_arg=True)
        in slack_client.api_call.call_args_list
    )


def test_slack_api_call_no_client():
    sdp = DummySlackDeploymentProcess()
    sdp.slack_client = None

    resp = sdp.slack_api_call('some_arguments')

    assert not resp['ok']
    assert resp['error'] == 'Slack client does not exist'


def test_slack_api_call_error():
    sdp = DummySlackDeploymentProcess()
    slack_client = sdp.slack_client

    with mock.patch.object(
        slack_client, 'api_call', side_effect=ValueError('error msg'),
    ):
        resp = sdp.slack_api_call('some_arguments')

    assert not resp['ok']
    assert resp['error'] == 'ValueError: error msg'


def test_truncate_blocks_text():
    blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': 'A' * 5000}}]
    truncated = slack.truncate_blocks_text(blocks)
    assert truncated == [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': 'A' * 3000}}]


def test_is_relevant_event():
    # First event is useful
    assert slack.is_relevant_event(MIXED_EVENTS_STREAM[0]) is True
    # 2nd event should be ignored
    assert slack.is_relevant_event(MIXED_EVENTS_STREAM[1]) is False
