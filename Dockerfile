FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Garante, em tempo de build, que ffmpeg e ffprobe foram instalados
# corretamente e estao disponiveis no PATH. Se qualquer um dos dois nao
# existir ou nao executar, o build falha aqui, em vez de falhar de forma
# silenciosa em produção com erros como "Não foi possível ler a duração
# do vídeo (ffprobe)".
RUN command -v ffmpeg && ffmpeg -version && \
    command -v ffprobe && ffprobe -version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p app/storage/uploads app/storage/outputs app/storage/temp

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
