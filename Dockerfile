FROM python:3.12-slim

WORKDIR /app

# Copia requirements e installa dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il codice
COPY . .

# Il poller gira dalla cartella poller/
CMD ["python", "poller/poller.py"]
