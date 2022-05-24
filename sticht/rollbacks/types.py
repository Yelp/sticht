from typing import NamedTuple


class SplunkAuth(NamedTuple):
    host: str
    port: int
    username: str
    password: str
