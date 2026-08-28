FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    HF_HOME=/tmp/huggingface \
    EASYOCR_MODULE_PATH=/tmp/easyocr

WORKDIR /app
RUN addgroup --system ledgerpilot && adduser --system --ingroup ledgerpilot ledgerpilot
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.8.0" "torchvision==0.23.0" \
    && python -c "import tomllib; print(chr(10).join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt
COPY README.md ./
COPY app ./app
RUN pip install --no-deps .
USER ledgerpilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
