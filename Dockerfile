FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the bot code (not the Replit PNPM workspace)
COPY bot/ ./

# Create data directory for JSON stores
RUN mkdir -p /app/data

CMD ["python3", "main.py"]
