FROM python:3.12-slim
WORKDIR /app
# ffmpeg : remux -c copy de la relecture mono-piste (api/recordings.py), jamais de ré-encodage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# gunicorn et non runserver (serveur de développement). Réglages et raisons :
# gunicorn.conf.py.
CMD ["sh", "-c", "python manage.py migrate --no-input && exec gunicorn -c gunicorn.conf.py config.wsgi:application"]
