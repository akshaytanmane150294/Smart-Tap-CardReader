FROM python:3.12.1-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libpcsclite-dev pcscd swig
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD service pcscd start && python app.py

