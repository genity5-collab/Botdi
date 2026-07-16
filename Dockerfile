FROM python:3.13-slim

WORKDIR /app

COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./

RUN mkdir -p /app/data

CMD ["python3", "main.py"]
