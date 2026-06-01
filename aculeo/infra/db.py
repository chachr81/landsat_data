from contextlib import contextmanager
from typing import Dict, Optional
from urllib.parse import urlparse, urlunparse

import paramiko
if not hasattr(paramiko, 'DSSKey'):
    paramiko.DSSKey = None
from sshtunnel import SSHTunnelForwarder

from .config import load_env


@contextmanager
def get_ssh_tunnel(env: Dict[str, str], remote_port_key: str = 'DB_PORT'):
    ssh_host = env.get('SSH_HOST')
    ssh_user = env.get('SSH_USER')
    ssh_key  = env.get('SSH_KEY_PATH')
    remote_port = int(env.get(remote_port_key, 5432))

    if not all([ssh_host, ssh_user, ssh_key]):
        raise ValueError("Faltan SSH_HOST, SSH_USER o SSH_KEY_PATH en .env")

    with SSHTunnelForwarder(
        (ssh_host, 22),
        ssh_username=ssh_user,
        ssh_pkey=ssh_key,
        remote_bind_address=('127.0.0.1', remote_port),
    ) as tunnel:
        yield tunnel.local_bind_port


def get_db_connection_string(env: Optional[Dict] = None, local_port: Optional[int] = None) -> str:
    if env is None:
        env = load_env()
    db_url = env.get('DB_URL')
    if not db_url:
        raise ValueError("Falta DB_URL en .env")

    parsed = urlparse(db_url)
    netloc_parts = parsed.netloc.split('@')
    auth_part = netloc_parts[0] + '@' if len(netloc_parts) > 1 else ''

    host = '127.0.0.1' if local_port else parsed.hostname
    port = local_port  if local_port else parsed.port

    new_parsed = parsed._replace(
        netloc=f"{auth_part}{host}:{port}",
        path='/maps_negentropy',
    )
    return urlunparse(new_parsed)