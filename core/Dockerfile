FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    anthropic \
    pyjwt \
    pyyaml \
    httpx \
    bcrypt \
    croniter \
    python-multipart \
    stripe \
    cryptography

# Copy core files
COPY main.py .
COPY memory.py .
COPY agent_loop.py .
COPY api_router.py .
COPY token_integration.py .
COPY tools/ ./tools/
COPY static/ ./static/

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
