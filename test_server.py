from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastmcp import Client
import server


def test_mcp_tools():
    import asyncio

    queries = []
    @contextmanager
    def fake_db():
        yield object()

    def fake_fetch(connection, sql, params=()):
        queries.append((sql, params))
        if 'information_schema.TABLES' in sql:
            return [{'name': 'orders', 'type': 'BASE TABLE'}]
        if 'information_schema.COLUMNS' in sql:
            return [{'name': 'id', 'type': 'int'}, {'name': 'amount', 'type': 'decimal'}]
        return [{'id': 1, 'amount': Decimal('12.50')}]

    async def run():
        async with Client(server.create_server()) as client:
            assert {t.name for t in await client.list_tools()} == {'list_tables', 'describe_table', 'read_rows'}
            result = await client.call_tool('list_tables', {})
            assert result.structured_content['result'][0]['name'] == 'orders'
            result = await client.call_tool('describe_table', {'table': 'orders'})
            assert result.structured_content['result'][0]['name'] == 'id'
            result = await client.call_tool('read_rows', {'table': 'orders', 'selected_columns': ['id', 'amount'], 'filters': {'id': 1}, 'limit': 10})
            assert result.structured_content['result'] == [{'id': 1, 'amount': '12.50'}]
            assert queries[-1][1] == (1, 10)
            assert '`id` = %s' in queries[-1][0]
            for args in [
                {'table': 'orders; DROP TABLE orders', 'selected_columns': ['id']},
                {'table': 'orders', 'selected_columns': ['id; DROP TABLE orders']},
                {'table': 'orders', 'selected_columns': ['id'], 'limit': 501},
                {'table': 'orders', 'selected_columns': []},
            ]:
                with pytest.raises(Exception):
                    await client.call_tool('read_rows', args)
    with patch.object(server, 'database', fake_db), patch.object(server, 'fetch', fake_fetch):
        asyncio.run(run())


def test_database_tls_and_read_only(monkeypatch):
    for name in ['MYSQL_HOST', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE']:
        monkeypatch.setenv(name, 'test')
    monkeypatch.setenv('MYSQL_SSL_CA', '/app/certs/rds-us-east-1-bundle.pem')
    with patch.object(server.mysql.connector, 'connect') as connect:
        with server.database():
            pass
        assert connect.call_args.kwargs['use_pure'] is True
        assert connect.call_args.kwargs['ssl_ca'] == '/app/certs/rds-us-east-1-bundle.pem'
        assert connect.call_args.kwargs['ssl_verify_identity'] is True
        assert connect.call_args.kwargs['ssl_verify_cert'] is True
        connect.return_value.start_transaction.assert_called_once_with(readonly=True)
        connect.return_value.close.assert_called_once()


def test_remote_fails_without_auth(monkeypatch):
    monkeypatch.setenv('MCP_TRANSPORT', 'http')
    monkeypatch.delenv('OAUTH_ISSUER', raising=False)
    with pytest.raises(RuntimeError, match='OAUTH_ISSUER'):
        server.main()


def test_auth0_proxy_discovery_and_registration(monkeypatch, tmp_path):
    import fastmcp
    import fakeredis.aioredis
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
    from starlette.testclient import TestClient

    issuer = 'https://tenant.auth0.com/'
    for name, value in {
        'MCP_TRANSPORT': 'http',
        'OAUTH_ISSUER': issuer,
        'OAUTH_AUDIENCE': 'https://easykash-mcp',
        'MCP_PUBLIC_URL': 'https://easykashmcp.onrender.com',
        'AUTH0_CLIENT_ID': 'test-client',
        'AUTH0_CLIENT_SECRET': 'test-only-not-a-real-secret',
        'REDIS_URL': 'rediss://redis.example.com:6379/0',
        'OAUTH_STORAGE_KEY': Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(fastmcp.settings, 'home', tmp_path)
    configuration = OIDCConfiguration(
        issuer=issuer,
        response_types_supported=['code'],
        subject_types_supported=['public'],
        id_token_signing_alg_values_supported=['RS256'],
        authorization_endpoint=issuer + 'authorize',
        token_endpoint=issuer + 'oauth/token',
        jwks_uri=issuer + '.well-known/jwks.json',
    )
    shared_redis = fakeredis.FakeServer()
    monkeypatch.setattr(server.Redis, 'from_url', lambda *a, **kw: fakeredis.aioredis.FakeRedis(server=shared_redis, decode_responses=True))
    with patch.object(server.Auth0Provider, 'get_oidc_configuration', return_value=configuration), \
            patch.object(server, 'create_server') as create:
        server.main()
        auth = create.call_args.args[0]
        assert auth._extra_authorize_params['audience'] == 'https://easykash-mcp'
        assert auth._extra_token_params['audience'] == 'https://easykash-mcp'
        assert auth._redirect_path == '/auth/callback'
    app = server.create_server(auth).http_app(stateless_http=True)
    with TestClient(app) as client:
        metadata = client.get('/.well-known/oauth-authorization-server')
        assert metadata.status_code == 200
        data = metadata.json()
        assert data['authorization_endpoint'].startswith('https://easykashmcp.onrender.com/')
        assert 'S256' in data['code_challenge_methods_supported']
        assert 'mysql:read' in data['scopes_supported']
        registration = client.post('/register', json={
            'client_name': 'test-chatgpt',
            'redirect_uris': ['https://chatgpt.com/connector_platform/oauth/callback'],
            'token_endpoint_auth_method': 'none',
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
        })
        assert registration.status_code == 201, registration.text
        registered_id = registration.json()['client_id']
        assert registered_id
        response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                               headers={'Accept': 'application/json, text/event-stream'})
        assert response.status_code == 401

    # A new provider has no in-memory registration from the first process.
    with patch.object(server.Auth0Provider, 'get_oidc_configuration', return_value=configuration), \
            patch.object(server, 'create_server') as create:
        server.main()
        restarted_auth = create.call_args.args[0]
    assert restarted_auth is not auth
    with TestClient(server.create_server(restarted_auth).http_app(stateless_http=True)) as client:
        response = client.get('/authorize', params={
            'client_id': registered_id,
            'redirect_uri': 'https://chatgpt.com/connector_platform/oauth/callback',
            'response_type': 'code', 'scope': 'mysql:read',
            'state': 'test-state', 'code_challenge': 'a' * 43,
            'code_challenge_method': 'S256',
        })
        assert response.status_code == 200, response.text
        assert 'Client Not Registered' not in response.text

    import asyncio
    async def verify_encrypted():
        client = fakeredis.aioredis.FakeRedis(server=shared_redis, decode_responses=True)
        values = [await client.get(key) for key in await client.keys('*')]
        assert values
        assert all('test-chatgpt' not in value for value in values)
        await client.aclose()
    asyncio.run(verify_encrypted())


@pytest.mark.parametrize('url', ['redis://public.example.com:6379', 'https://redis.example.com'])
def test_oauth_storage_requires_tls(monkeypatch, url):
    monkeypatch.setenv('REDIS_URL', url)
    with pytest.raises(RuntimeError, match='rediss://'):
        server.oauth_storage()


def test_oauth_storage_requires_stable_key(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'rediss://redis.example.com:6379')
    monkeypatch.setenv('OAUTH_STORAGE_KEY', 'not-a-valid-key')
    with pytest.raises(RuntimeError, match='valid Fernet key'):
        server.oauth_storage()


def test_redis_tls_client_options(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv('REDIS_URL', 'rediss://redis.example.com:6379')
    monkeypatch.setenv('OAUTH_STORAGE_KEY', Fernet.generate_key().decode())
    storage = server.oauth_storage()
    client = storage.key_value._client
    options = client.connection_pool.connection_kwargs
    assert options['ssl_cert_reqs'] == 'required'
    assert options['ssl_check_hostname'] is True


def test_redis_url_cannot_override_tls_verification(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'rediss://redis.example.com?ssl_cert_reqs=none')
    with pytest.raises(RuntimeError, match='query parameters'):
        server.oauth_storage()
