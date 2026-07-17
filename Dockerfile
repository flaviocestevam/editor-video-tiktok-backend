FROM python:3.11-slim

# Instala FFmpeg, fontconfig e a fonte Lato usada nos textos dos Reels.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fontconfig \
    fonts-lato \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cria diretório de storage
RUN mkdir -p storage/uploads storage/outputs storage/temp

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
