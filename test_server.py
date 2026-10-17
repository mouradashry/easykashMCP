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
    with patch.object(server.mysql.connector, 'connect') as connect:
        with server.database():
            pass
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
        assert registration.json()['client_id']
        response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                               headers={'Accept': 'application/json, text/event-stream'})
        assert response.status_code == 401
