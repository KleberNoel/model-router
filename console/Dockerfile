FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY console ./console

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir ".[console]"

EXPOSE 8501

CMD ["streamlit", "run", "console/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
