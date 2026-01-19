# Image Python Linux légère
FROM python:3.9-slim

# Dossier de travail
WORKDIR /app

# Installation Git (parfois requis)
RUN apt-get update && apt-get install -y git

# Copie des fichiers
COPY . .

# Installation
RUN pip install --no-cache-dir -r requirements.txt

# Port Chainlit
EXPOSE 7860

# Lancement
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "7860"]