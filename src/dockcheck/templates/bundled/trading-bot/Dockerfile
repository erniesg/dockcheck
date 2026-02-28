FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Do not expose external ports — trading bots communicate via internal message bus
CMD ["python", "-m", "bot.main"]
