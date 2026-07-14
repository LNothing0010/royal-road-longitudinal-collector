FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[api,mcp]"
ENV RR_DB_PATH=/data/rrlab.sqlite RR_RAW_DIR=/data/raw RR_REPORT_DIR=/data/reports
VOLUME ["/data"]
CMD ["uvicorn", "rrlab.api:app", "--host", "0.0.0.0", "--port", "8080"]
