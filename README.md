# MySQL MCP for ChatGPT

A read-only Python MCP server with three tools:

- `list_tables`: discover tables/views in one configured database.
- `describe_table`: inspect columns, types, and keys.
- `read_rows`: select explicit columns with optional equality filters, capped at 500 rows.

It does not execute arbitrary SQL. Identifiers are checked against database metadata and quoted; values use bound parameters. Each connection starts a read-only transaction. Use a dedicated MySQL account granted **SELECT only** on the specific tables ChatGPT should see. Data returned by these tools is sent to the connected client; choose grants accordingly.

## Develop

Python 3.12+ and uv are required:

```sh
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen
uv run pytest -q
```

Use the existing checkout; a separate Git worktree is unnecessary.

Set the MySQL variables in `.env.example` through your host's secret settings or shell environment. The example file is a template; the server does not load it automatically. Never commit passwords. TLS certificate and hostname verification are required. For RDS, obtain the current regional CA bundle from the official Amazon RDS trust store and set `MYSQL_SSL_CA` to its local path. Do not disable certificate verification.

The intended database host is `easykashbackendreplica.ct8lliybav4s.us-east-1.rds.amazonaws.com`. The database/schema is `easykash-live`; a restricted account still needs configuration. Host the server where it can reach RDS on TCP 3306, preferably inside the appropriate AWS VPC. Keep RDS private and allow traffic from the server's security group.

Local MCP clients can launch `.venv/bin/python /absolute/path/to/server.py` using the default `stdio` transport with database variables injected. Stdio is for local clients; ChatGPT requires the remote endpoint below.

## Connect ChatGPT

GitHub stores the source code; GitHub Pages cannot run this Python service, and GitHub Actions is not a persistent server host. Use a runtime such as AWS ECS or EC2 with access to RDS.

1. Deploy the server behind HTTPS and configure the database variables. The Dockerfile provides a deployment starting point.
2. Configure an OAuth authorization server compatible with ChatGPT's MCP OAuth flow. It must issue RS256 JWT access tokens, expose OAuth discovery and JWKS, grant the `mysql:read` scope, and support client registration or a pre-registered ChatGPT client as required by your ChatGPT setup.
3. Set `MCP_TRANSPORT=http`, `MCP_PUBLIC_URL` (the public HTTPS origin), `OAUTH_ISSUER` (exact token issuer), `OAUTH_JWKS_URI`, and `OAUTH_AUDIENCE` (the API audience). Set `MYSQL_SSL_CA` and mount its CA file in the container when applicable.
4. Run `.venv/bin/python server.py`, or deploy the container on port 8000. HTTP startup fails if OAuth settings are missing. Terminate TLS at the hosting platform/reverse proxy. The service exposes MCP at `/mcp` and OAuth protected-resource metadata through FastMCP.
5. In ChatGPT's apps/connectors developer settings, add the public `https://YOUR_HOST/mcp` URL and complete OAuth sign-in. Availability and client configuration depend on your ChatGPT plan/workspace settings.
6. Ask ChatGPT to list tables, describe an allowed table, and read a small set of chosen columns. Verify the result against MySQL.

The OAuth service itself is not provided by this repository. An identity provider and public hosting are prerequisites for ChatGPT access. There is no unauthenticated remote mode.

## Validation status

`pytest` exercises MCP tool discovery and calls using a mocked database, identifier/row-limit rejection, parameter binding, TLS/read-only connection setup, and failure when HTTP authentication settings are absent. These checks do not establish connectivity to RDS or completion of a ChatGPT OAuth flow. Those require deployment settings and credentials.
