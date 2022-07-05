import yaml

from sticht.rollbacks.metrics import watch_metrics_for_service
from sticht.rollbacks.sources.splunk import SplunkMetricWatcher
from sticht.rollbacks.types import SplunkAuth

TEST_SPLUNK_AUTH = SplunkAuth(
    host='splank.yelp.com',
    port=1234,
    username='username',
    password='totally_a_password',
)


def test_watch_metrics_for_service_creates_watchers(tmp_path):
    service = 'serviceA'
    soa_dir = tmp_path
    (soa_dir / service).mkdir()
    (soa_dir / service / 'rollback-test-cluster.yaml').write_text(
        yaml.safe_dump(
            {
                'conditions': {
                    'splunk': [
                        {
                            'label': 'label',
                            'query': 'hwat',
                            'lower_bound': 1,
                        },
                    ],
                },
            },
        ),
    )

    _, watchers = watch_metrics_for_service(
        service=service,
        soa_dir=soa_dir,
        on_failure_callback=lambda _, __: None,
        on_failure_trigger_callback=lambda _: None,
        splunk_auth=TEST_SPLUNK_AUTH,
    )

    assert len(watchers) == 1
    assert watchers[0] == SplunkMetricWatcher(
        label='label',
        query='hwat',
        on_failure_callback=lambda _: None,
        splunk=TEST_SPLUNK_AUTH,
    )
