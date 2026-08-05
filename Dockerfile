FROM python:3.12-slim

WORKDIR /app

# Copia requirements e installa dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutto il codice
COPY . .

# Avvia sia il poller che Streamlit
# Il poller gira in background, Streamlit in foreground sulla porta $PORT
CMD python poller/poller.py & streamlit run dashboard/app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true
