import pytest

from sticht.rollbacks.soaconfigs import get_cluster_from_soaconfigs_filename
from sticht.rollbacks.soaconfigs import get_rollback_files_from_soaconfigs


@pytest.mark.parametrize(
    'dir_files, expected_files', (
        # single service with a single rollback file
        (
            (
                'serviceA/kubernetes-test.yaml',
                'serviceA/slo-test.yaml',
                'serviceA/rollback-test.yaml',
            ),
            ('serviceA/rollback-test.yaml',),
        ),
        # multiple services with a single rollback file
        (
            (
                'serviceA/kubernetes-test.yaml',
                'serviceA/slo-test.yaml',
                'serviceA/rollback-test.yaml',
                'serviceB/rollback-test.yaml',
            ),
            ('serviceA/rollback-test.yaml', 'serviceB/rollback-test.yaml'),
        ),
        # single service with multiple rollbacks
        (
            (
                'serviceA/rollback-test.yaml',
                'serviceA/rollback-test2.yaml',
            ),
            ('serviceA/rollback-test.yaml', 'serviceA/rollback-test2.yaml'),
        ),
        # file with an incorrect rollback prefix
        (
            (
                'serviceA/rollbacknot-test.yaml',
                'serviceA/rollbacknope-test2.yaml',
            ),
            tuple(),
        ),
        # empty directory
        (
            tuple(),
            tuple(),
        ),
    ),
)
def test_get_rollback_files_from_soaconfigs(dir_files, expected_files, tmp_path):
    for dir_file in dir_files:
        directory, file = dir_file.split('/')
        (tmp_path / directory).mkdir(exist_ok=True)
        (tmp_path / directory / file).touch()

    assert sorted(
        get_rollback_files_from_soaconfigs(soaconfigs_path=tmp_path),
    ) == sorted(
        str(tmp_path / file) for file in expected_files
    )


@pytest.mark.parametrize(
    'filename, expected_cluster', (
        ('/nail/etc/services/serviceA/rollback-pnw-devc.yaml', 'pnw-devc'),
        ('/nail/etc/services/serviceA/slos-pnw-prod.yaml', 'pnw-prod'),
        ('/nail/etc/services/serviceA/kubernetes-pnwstage.yaml', 'pnwstage'),
        ('/my/soaconf/dir/serviceA/rollback-spark-pnw-something.yaml', 'spark-pnw-something'),
    ),
)
def test_get_cluster_from_soaconfigs_filename(filename, expected_cluster):
    get_cluster_from_soaconfigs_filename(filename) == expected_cluster
