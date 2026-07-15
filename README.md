# Editor Vídeo TikTok - Backend

Backend em FastAPI para um editor criativo de vídeos curtos, feito para uso pessoal. Permite enviar um arquivo MP4 ou colar um link de um vídeo curto (TikTok, YouTube Shorts ou Instagram), baixá-lo automaticamente com yt-dlp e aplicar melhorias criativas automáticas usando ffmpeg antes de re-encodar o resultado final.

## Funcionalidades

- Upload manual de arquivos MP4.
- Download automático de vídeos a partir de links do TikTok, YouTube Shorts ou Instagram, usando yt-dlp.
- Flip horizontal do vídeo.
- Pequenos cortes aleatórios no início e no fim.
- Crop com zoom suave.
- Alteração sutil de velocidade (vídeo e áudio).
- Ajustes de brilho, contraste e saturação.
- Opção para remover o áudio.
- Fade de entrada e de saída.
- Re-encode completo do vídeo em H.264/AAC.
- Limpeza automática de arquivos temporários gerados durante o processamento.

## Estrutura do projeto

```
.
├── main.py
├── requirements.txt
├── Dockerfile
├── .gitignore
└── app/
    ├── __init__.py
    ├── routers/
    │   ├── __init__.py
    │   └── video.py
    └── services/
        ├── __init__.py
        ├── downloader.py
        └── video_processor.py
```

## Requisitos

- Python 3.11 ou superior.
- ffmpeg e ffprobe instalados e disponíveis no PATH do sistema.

## Rodando localmente

1. Clone o repositório:

```bash
git clone https://github.com/flaviocestevam/editor-video-tiktok-backend.git
cd editor-video-tiktok-backend
```

2. Crie e ative um ambiente virtual:

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. Instale as dependências:

```bash
pip install -r requirements.txt
```

4. Instale o ffmpeg (se ainda não tiver):

- Ubuntu/Debian: `sudo apt-get install ffmpeg`
- macOS (Homebrew): `brew install ffmpeg`
- Windows: baixe em https://ffmpeg.org/download.html e adicione ao PATH.

5. Inicie o servidor:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

6. Acesse a documentação interativa em http://localhost:8000/docs

## Endpoints principais

- `POST /api/video/upload` — envia um arquivo MP4 (multipart/form-data, campo `file`).
- `POST /api/video/download` — baixa um vídeo a partir de um link (form-data, campo `url`).
- `POST /api/video/process` — aplica as melhorias criativas sobre o `file_id` retornado no upload/download.
- `GET /api/video/result/{filename}` — baixa o vídeo processado.
- `DELETE /api/video/cleanup/{file_id}` — remove manualmente um arquivo de upload temporário.

## Deploy no Railway

1. Faça login em https://railway.com e clique em "New Project".
2. Escolha a opção "Deploy from GitHub repo" e selecione o repositório `editor-video-tiktok-backend`.
3. O Railway detecta o `Dockerfile` automaticamente e usa ele para o build (garante que o ffmpeg seja instalado na imagem).
4. Em "Settings" do serviço, defina a variável `PORT` caso necessário (o Railway já injeta essa variável automaticamente; o Dockerfile já expõe a porta 8000, mas o Uvicorn pode ser configurado para usar `$PORT` se preferir).
5. Confirme o deploy. Após o build, o Railway fornece uma URL pública para acessar a API.
6. Teste acessando `https://SEU-DOMINIO.up.railway.app/docs`.

## Observações importantes

- Este projeto é destinado a uso pessoal. Respeite os termos de uso das plataformas de origem ao baixar vídeos de terceiros.
- Os arquivos de upload e de saída são armazenados em `app/storage/` e devem ser tratados como temporários; considere integrar um storage externo (S3, GCS, etc.) para uso em produção.
- Ajuste o `MAX_FILE_SIZE_MB` em `app/routers/video.py` conforme a necessidade do seu ambiente.
