from aculeo.infra.db import get_db_connection_string


def test_respects_db_url_path():
    """Si DB_URL tiene un path distinto de maps_negentropy, debe respetarlo."""
    env = {'DB_URL': 'postgresql://user:pass@host:5432/test_db'}
    result = get_db_connection_string(env, local_port=None)
    assert '/test_db' in result
    assert 'maps_negentropy' not in result


def test_fallback_to_maps_negentropy_when_no_path():
    env = {'DB_URL': 'postgresql://user:pass@host:5432'}
    result = get_db_connection_string(env, local_port=None)
    assert '/maps_negentropy' in result


def test_fallback_when_root_path():
    env = {'DB_URL': 'postgresql://user:pass@host:5432/'}
    result = get_db_connection_string(env, local_port=None)
    assert '/maps_negentropy' in result


def test_local_port_replaces_host():
    env = {'DB_URL': 'postgresql://user:pass@remote-host:5432/maps_negentropy'}
    result = get_db_connection_string(env, local_port=54320)
    assert '127.0.0.1:54320' in result
    assert 'remote-host' not in result
