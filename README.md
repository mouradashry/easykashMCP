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

Set the MySQL variables in `.env.example` through your host's secret settings or shell environment. The example file is a template; the server does not load it automatically. Never commit passwords. TLS certificate and hostname verification are required. The Docker image uses the Python base image’s system CA certificates and downloads the official us-east-1 RDS bundle over verified HTTPS from `truststore.pki.rds.amazonaws.com`, checks that it parses as a certificate bundle, and sets `MYSQL_SSL_CA=/app/certs/rds-us-east-1-bundle.pem`. Remove any conflicting Render override or set it to that exact path. Native Python deployments must obtain the regional RDS CA bundle and set `MYSQL_SSL_CA` to its local path. The server uses the pure-Python MySQL connector to avoid bundled C-client trust-path errors. Do not disable certificate verification.

The intended database host is `easykashbackendreplica.ct8lliybav4s.us-east-1.rds.amazonaws.com`. The database/schema is `easykash-live`; a restricted account still needs configuration. Host the server where it can reach RDS on TCP 3306, preferably inside the appropriate AWS VPC. Keep RDS private and allow traffic from the server's security group.

Local MCP clients can launch `.venv/bin/python /absolute/path/to/server.py` using the default `stdio` transport with database variables injected. Stdio is for local clients; ChatGPT requires the remote endpoint below.

## Connect ChatGPT

GitHub stores the source code; GitHub Pages cannot run this Python service, and GitHub Actions is not a persistent server host. Use a runtime such as AWS ECS or EC2 with access to RDS.

1. Deploy the server behind HTTPS and configure the database variables. The Dockerfile provides a deployment starting point.
2. In Auth0 create a **Regular Web Application** for the MCP server. Enable its Authorization Code grant. Set **Allowed Callback URLs** to `https://easykashmcp.onrender.com/auth/callback` (or your own public origin plus `/auth/callback`). This is the server's upstream callback, not ChatGPT's callback. Keep the application client secret in Render's secret environment settings only.
3. Create an Auth0 API with identifier `https://easykash-mcp`, RS256 signing, and permission `mysql:read`. Restrict who can sign into the application: for a personal server use an approved account and disable public signup on the connected database connection. The permission's existence alone does not enforce user-specific database access.
4. Configure Render's environment:

   ```text
   MCP_TRANSPORT=http
   MCP_PUBLIC_URL=https://easykashmcp.onrender.com
   OAUTH_ISSUER=https://dev-sg8epu3k08rfskl8.us.auth0.com/
   OAUTH_AUDIENCE=https://easykash-mcp
   AUTH0_CLIENT_ID=<Auth0 Regular Web Application client ID>
   AUTH0_CLIENT_SECRET=<enter securely in Render>
   REDIS_URL=<rediss://username:password@redis-host:port/0>
   OAUTH_STORAGE_KEY=<stable generated Fernet key>
   ```

   `OAUTH_JWKS_URI` is no longer needed: `Auth0Provider` discovers the upstream configuration and sends the API audience during authorization and token exchange. Keep the MySQL variables and trusted CA file configured.
5. Deploy the container. `Auth0Provider` must retrieve Auth0's HTTPS discovery configuration at startup. The service exposes MCP at `/mcp`, OAuth discovery, dynamic client registration, and `/auth/callback`. Consent remains enabled. OAuth registrations, pending flows, and tokens are stored in external Redis with Fernet encryption. HTTP startup requires `REDIS_URL` and `OAUTH_STORAGE_KEY`; it never falls back to ephemeral disk. Keep the Auth0 client secret stable because it derives the proxy's default JWT signing key.

   Create a Redis database with persistent storage, for example a managed Redis service accessible from Render. Use its **Redis protocol TLS URL** (`rediss://`), not an HTTPS REST API URL. The implementation preserves TLS with hostname/certificate verification, Redis ACL username, password and database selection. URL query parameters are rejected to prevent disabling TLS verification. Set a no-eviction policy where supported; provider expiry, eviction, deletion, or loss of Redis data can still invalidate sessions. Use a dedicated database for this deployment, and keep the same Redis database and encryption key across releases.

   Generate the encryption key **once** in your local terminal or a trusted shell with the dependencies installed:

   ```sh
   .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Paste it directly into Render's secret environment settings as `OAUTH_STORAGE_KEY`; do not send it in chat or commit it. Set `REDIS_URL` securely there too. Changing the key makes previously encrypted state unreadable. The Redis host must be reachable from Render and its CA must be trusted. Redis availability is required during OAuth operations.

6. After the first migration to Redis, remove the old ChatGPT connection and create a fresh OAuth connection to `https://easykashmcp.onrender.com/mcp`, allowing automatic client registration when offered. Do not enter the upstream Auth0 application secret into ChatGPT: the server holds that secret and handles Auth0; ChatGPT registers with the MCP proxy. The proxy handles forwarding back to ChatGPT after the Auth0 callback.
7. Sign in through Auth0 and complete the consent flow, then verify discovery of `list_tables`, `describe_table`, and `read_rows`. Read a small authorized result and compare it against MySQL.

An Auth0 account, public HTTPS hosting, and network access to RDS remain prerequisites. There is no unauthenticated remote mode. The tools support table inspection and filtered row reads; arbitrary SQL and aggregation queries are not implemented.

## Validation status

`pytest` exercises MCP tool discovery and calls using a mocked database, identifier/row-limit rejection, parameter binding, TLS/read-only connection setup, and failure when HTTP authentication settings are absent. An additional local HTTP test uses mocked Auth0 discovery and a shared fake Redis server to verify registration survives provider recreation, stored values are encrypted, and audience forwarding, OAuth metadata/PKCE, dynamic registration, and rejection of unauthenticated MCP requests. These checks do not establish connectivity to RDS or completion of a ChatGPT OAuth flow. Those require deployment settings and credentials.
