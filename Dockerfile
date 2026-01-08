FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY paperboy/ ./paperboy/

EXPOSE 8000

CMD ["uvicorn", "paperboy.main:app", "--host", "0.0.0.0", "--port", "8000"]