FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml server.py ./
RUN pip install --no-cache-dir .
USER 65532:65532
ENV MCP_TRANSPORT=http PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
