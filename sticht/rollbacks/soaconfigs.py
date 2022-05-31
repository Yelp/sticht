import glob
import os
from typing import List

DEFAULT_SOA_DIR = '/nail/etc/services'


def get_cluster_from_soaconfigs_filename(filename: str) -> str:
    """Given a file like {type}-{cluster}.yaml, returns {cluster}"""
    basename, _ = os.path.splitext(os.path.basename(filename))
    _, cluster = basename.split('-', 1)

    return cluster


def get_rollback_files_from_soaconfigs(soaconfigs_path: str = DEFAULT_SOA_DIR, service=None) -> List[str]:
    """Get the full path to all autorollback files in soaconfigs"""
    directory = '**' if service is None else service
    return [
        rollback_path
        for rollback_path in glob.glob(
            os.path.join(soaconfigs_path, directory, 'rollback-*.yaml'), recursive=False,
        )
    ]
