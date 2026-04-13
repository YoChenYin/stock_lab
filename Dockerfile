FROM python:3.11-slim
LABEL "language"="python"
LABEL "framework"="streamlit"

# Flush Python stdout/stderr immediately — required to see runtime logs
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Use $PORT from Zeabur (defaults to 8080 if not set)
CMD ["sh", "-c", "streamlit run main.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]
