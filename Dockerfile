FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
# Fail the image build immediately on Python syntax/indentation errors.
RUN python -m compileall -q app && find /app/app -type d -name __pycache__ -prune -exec rm -rf {} + && find /app/app -type f -name '*.pyc' -delete
RUN rm -f /app/web/assets/sas-pro-home.png
COPY .env.example ./.env.example
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
