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
    monkeypatch.delenv('OAUTH_JWKS_URI', raising=False)
    with pytest.raises(RuntimeError, match='OAUTH_JWKS_URI'):
        server.main()
