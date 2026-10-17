FROM python:3.12-slim
RUN mkdir -p /app/certs \
    && python -c "import ssl, urllib.request, pathlib; data = urllib.request.urlopen('https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem', timeout=30).read(); context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.load_verify_locations(cadata=data.decode('ascii')); pathlib.Path('/app/certs/rds-us-east-1-bundle.pem').write_bytes(data)" \
    && chmod 644 /app/certs/rds-us-east-1-bundle.pem
WORKDIR /app
COPY pyproject.toml server.py ./
RUN pip install --no-cache-dir .
USER 65532:65532
ENV MCP_TRANSPORT=http PORT=8000 FASTMCP_HOME=/tmp/fastmcp \
    MYSQL_SSL_CA=/app/certs/rds-us-east-1-bundle.pem
EXPOSE 8000
CMD ["python", "server.py"]
