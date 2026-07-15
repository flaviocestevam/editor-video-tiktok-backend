import os
import random
import re
import json
import subprocess
import logging
import time

logger = logging.getLogger("editor_video_tiktok.processor")

# Tempo maximo (em segundos) que uma chamada ao ffmpeg/ffprobe pode levar
# antes de ser considerada travada e abortada. Pode ser ajustado via
# variavel de ambiente FFMPEG_TIMEOUT_SECONDS.
FFMPEG_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "180"))
FFPROBE_TIMEOUT_SECONDS = int(os.getenv("FFPROBE_TIMEOUT_SECONDS", "30"))


class VideoProcessingError(Exception):
    """Erro ao processar um vídeo com o ffmpeg."""


def _run_ffmpeg(cmd, step_name="ffmpeg", timeout=FFMPEG_TIMEOUT_SECONDS):
    """Executa um comando ffmpeg com logging detalhado e timeout de seguranca.

    Loga claramente o inicio e o fim de cada etapa (com duracao), e converte
    qualquer falha, timeout ou ausencia do binario ffmpeg em uma
    VideoProcessingError com mensagem clara para ser exibida no frontend.
    """
    logger.info("[%s] Iniciando comando ffmpeg: %s", step_name, " ".join(cmd))
    start = time.monotonic()

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        logger.error(
            "[%s] TRAVOU: excedeu o tempo limite de %ss (rodou por %.1fs). "
            "O processo ffmpeg foi encerrado.",
            step_name, timeout, elapsed,
        )
        raise VideoProcessingError(
            f"O processamento travou na etapa '{step_name}' e excedeu o tempo "
            f"limite de {timeout}s. Tente novamente com um vídeo menor ou "
            "verifique os logs do servidor."
        ) from exc
    except FileNotFoundError as exc:
        logger.error(
            "[%s] Binário não encontrado no PATH do servidor (%s).",
            step_name, exc,
        )
        raise VideoProcessingError(
            "ffmpeg não está instalado ou não foi encontrado no PATH do servidor."
        ) from exc
    except Exception as exc:
        logger.exception("[%s] Erro inesperado ao executar ffmpeg", step_name)
        raise VideoProcessingError(
            f"Erro inesperado na etapa '{step_name}': {exc}"
        ) from exc

    elapsed = time.monotonic() - start

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore")
        logger.error(
            "[%s] Falhou após %.1fs (código de saída %s). stderr: %s",
            step_name, elapsed, result.returncode, stderr[-1000:],
        )
        raise VideoProcessingError(
            f"Falha ao processar vídeo na etapa '{step_name}': {stderr[-500:]}"
        )

    logger.info("[%s] Concluído com sucesso em %.1fs", step_name, elapsed)
    return result


def _ffprobe_json(path: str, timeout: int = FFPROBE_TIMEOUT_SECONDS):
    """Roda ffprobe pedindo format+streams em JSON.

    Retorna uma tupla (data, stderr). "data" é um dict (já decodificado do
    JSON) em caso de sucesso, ou None se o ffprobe falhar, travar ou
    retornar um JSON inválido. "stderr" sempre é retornado (mesmo em caso
    de sucesso) para permitir logar detalhes adicionais quando necessário.

    Levanta VideoProcessingError apenas para os casos irrecuperáveis:
    timeout e binário ausente no PATH.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json",
        path,
    ]
    logger.info("Executando ffprobe (json): %s", " ".join(cmd))
    start = time.monotonic()

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "ffprobe (json) travou/excedeu %ss ao analisar %s", timeout, path
        )
        raise VideoProcessingError(
            "Tempo limite excedido ao ler informações do vídeo (ffprobe). "
            "O arquivo pode estar corrompido ou ser muito grande."
        ) from exc
    except FileNotFoundError as exc:
        logger.error("ffprobe não encontrado no PATH do servidor.")
        raise VideoProcessingError(
            "ffprobe não está instalado ou não foi encontrado no PATH do servidor."
        ) from exc
    except Exception as exc:
        logger.exception("Erro inesperado ao executar ffprobe (json) em %s", path)
        raise VideoProcessingError(
            f"Erro inesperado ao consultar informações do vídeo: {exc}"
        ) from exc

    elapsed = time.monotonic() - start
    stderr = result.stderr.decode("utf-8", errors="ignore")

    if result.returncode != 0:
        logger.warning(
            "ffprobe (json) retornou código %s após %.2fs para %s. stderr: %s",
            result.returncode, elapsed, path, stderr[-500:],
        )
        return None, stderr

    raw_stdout = result.stdout.decode("utf-8", errors="ignore")
    try:
        data = json.loads(raw_stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.warning(
            "Falha ao decodificar JSON do ffprobe para %s: %s", path, exc
        )
        return None, stderr

    logger.info("ffprobe (json) concluído em %.2fs para %s", elapsed, path)
    return data, stderr


def _duration_from_ffmpeg_stderr(path: str, timeout: int = FFPROBE_TIMEOUT_SECONDS):
    """Fallback: usa o próprio ffmpeg para ler a linha "Duration: HH:MM:SS.xx"
    do stderr quando o ffprobe falha ou não retorna a duração. Isso cobre
    casos de containers um pouco atípicos ou arquivos com metadados
    parcialmente corrompidos que o ffprobe às vezes rejeita, mas que o
    ffmpeg ainda consegue interpretar a partir do cabeçalho.

    Retorna a duração em segundos (float) ou None se não for possível
    determiná-la por essa via também.
    """
    cmd = ["ffmpeg", "-hide_banner", "-i", path]
    logger.info("Tentando fallback de duração via 'ffmpeg -i' em: %s", path)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Fallback 'ffmpeg -i' travou/excedeu %ss ao analisar %s", timeout, path
        )
        return None
    except FileNotFoundError:
        logger.error("ffmpeg não encontrado no PATH ao tentar fallback de duração.")
        return None
    except Exception as exc:
        logger.warning("Erro inesperado no fallback 'ffmpeg -i' para %s: %s", path, exc)
        return None

    # "ffmpeg -i" sem saída sempre retorna código != 0, então o que importa
    # aqui é o conteúdo do stderr, não o returncode.
    stderr = result.stderr.decode("utf-8", errors="ignore")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        logger.warning(
            "Fallback 'ffmpeg -i' não encontrou 'Duration' no stderr para %s. "
            "Trecho do stderr: %s",
            path, stderr[-500:],
        )
        return None

    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    logger.info("Duração obtida via fallback 'ffmpeg -i': %.2fs para %s", duration, path)
    return duration


def _probe_duration(path: str) -> float:
    """Consulta a duração do vídeo com fallback robusto em múltiplas camadas.

    1) Tenta ffprobe pedindo format+streams em JSON (mais robusto do que
       pedir só "format=duration", pois alguns arquivos só têm a duração
       no stream de vídeo e não no container).
    2) Se o ffprobe falhar ou não trouxer uma duração utilizável, cai para
       o fallback de ler a linha "Duration" do stderr do próprio ffmpeg.
    3) Só levanta VideoProcessingError se todas as abordagens falharem,
       sempre logando os detalhes brutos (stderr) para facilitar o
       diagnóstico do problema real (arquivo corrompido, binário ausente,
       formato não suportado, etc.).
    """
    logger.info("Consultando duração do vídeo: %s", path)
    start = time.monotonic()

    data, ffprobe_stderr = _ffprobe_json(path)

    duration = None
    if data:
        fmt_duration = (data.get("format") or {}).get("duration")
        if fmt_duration:
            try:
                duration = float(fmt_duration)
            except (TypeError, ValueError):
                duration = None

        if not duration:
            for stream in data.get("streams", []) or []:
                stream_duration = stream.get("duration")
                if stream_duration:
                    try:
                        duration = float(stream_duration)
                        break
                    except (TypeError, ValueError):
                        continue

    if not duration:
        logger.warning(
            "ffprobe não retornou uma duração utilizável para %s (stderr: %s); "
            "tentando fallback via 'ffmpeg -i'.",
            path, (ffprobe_stderr or "")[-500:],
        )
        duration = _duration_from_ffmpeg_stderr(path)

    elapsed = time.monotonic() - start

    if not duration or duration <= 0:
        logger.error(
            "Não foi possível determinar a duração do vídeo %s após %.2fs "
            "mesmo com fallback (ffprobe stderr: %s)",
            path, elapsed, (ffprobe_stderr or "")[-500:],
        )
        raise VideoProcessingError(
            "Não foi possível ler a duração do vídeo (ffprobe). O arquivo pode "
            "estar corrompido, incompleto ou em um formato não suportado."
        )

    logger.info("Duração detectada: %.2fs (consulta levou %.2fs)", duration, elapsed)
    return duration


def validate_video_file(path: str) -> None:
    """Valida que o arquivo em "path" é um vídeo legível pelo ffprobe, com
    pelo menos uma stream de vídeo.

    Deve ser chamada logo após o upload ou o download de um arquivo, antes
    dele ser aceito para processamento. Levanta VideoProcessingError com
    mensagem clara se o arquivo estiver corrompido, incompleto ou não for
    um vídeo válido.
    """
    logger.info("Validando arquivo de vídeo recebido: %s", path)

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        logger.error("Validação falhou: arquivo ausente ou vazio (%s)", path)
        raise VideoProcessingError(
            "O arquivo enviado está vazio ou não foi salvo corretamente."
        )

    data, stderr = _ffprobe_json(path)

    if not data:
        logger.error(
            "Validação falhou: ffprobe não conseguiu ler %s. stderr: %s",
            path, (stderr or "")[-500:],
        )
        raise VideoProcessingError(
            "O arquivo enviado não é um vídeo válido ou está corrompido "
            "(ffprobe não conseguiu analisá-lo)."
        )

    streams = data.get("streams", []) or []
    has_video_stream = any(s.get("codec_type") == "video" for s in streams)

    if not has_video_stream:
        logger.error(
            "Validação falhou: nenhuma stream de vídeo encontrada em %s", path
        )
        raise VideoProcessingError(
            "O arquivo enviado não contém nenhuma stream de vídeo válida."
        )

    logger.info("Arquivo de vídeo validado com sucesso: %s", path)


def _clamp_atempo(factor: float) -> float:
    """O filtro atempo do ffmpeg aceita valores entre 0.5 e 2.0."""
    return max(0.5, min(2.0, factor))


def process_video(
    input_path: str,
    output_path: str,
    temp_dir: str,
    remove_audio: bool = False,
    flip_horizontal: bool = True,
    random_trim: bool = True,
    crop_zoom: bool = True,
    speed_change: bool = True,
    color_adjust: bool = True,
    fade: bool = True,
) -> str:
    """Aplica uma série de melhorias criativas automáticas e re-encoda o vídeo.

    Etapas aplicadas (quando habilitadas): cortes aleatórios no início/fim,
    flip horizontal, crop com zoom suave, ajuste sutil de velocidade,
    ajustes de brilho/contraste/saturação, remoção de áudio, fade de
    entrada e saída, e re-encode completo em H.264/AAC.

    Todas as etapas são logadas com timestamps para facilitar identificar
    exatamente onde o processamento eventualmente trava ou falha, e qualquer
    erro é convertido em VideoProcessingError com mensagem clara.
    """
    overall_start = time.monotonic()
    logger.info(
        "Iniciando processamento de vídeo | input=%s output=%s | opções: "
        "remove_audio=%s flip=%s random_trim=%s crop_zoom=%s speed_change=%s "
        "color_adjust=%s fade=%s",
        input_path, output_path, remove_audio, flip_horizontal, random_trim,
        crop_zoom, speed_change, color_adjust, fade,
    )

    if not os.path.exists(input_path):
        logger.error("Arquivo de entrada não encontrado: %s", input_path)
        raise VideoProcessingError("Arquivo de entrada não encontrado.")

    os.makedirs(temp_dir, exist_ok=True)
    temp_files = []

    try:
        duration = _probe_duration(input_path)

        start_cut = 0.0
        trim_duration = None
        if random_trim and duration > 3:
            start_cut = round(random.uniform(0.1, 0.6), 2)
            end_cut = round(random.uniform(0.1, 0.6), 2)
            trim_duration = max(duration - start_cut - end_cut, 1.0)
            duration = trim_duration
            logger.info(
                "Corte aleatório calculado: start_cut=%.2fs end_cut=%.2fs "
                "nova_duracao=%.2fs",
                start_cut, end_cut, trim_duration,
            )
        else:
            logger.info("Corte aleatório desabilitado ou vídeo curto demais; pulando etapa.")

        video_filters = []

        if flip_horizontal:
            video_filters.append("hflip")

        if crop_zoom:
            video_filters.append("crop=iw*0.92:ih*0.92")
            video_filters.append("scale=iw/0.92:ih/0.92")

        if color_adjust:
            brightness = round(random.uniform(-0.03, 0.03), 3)
            contrast = round(random.uniform(0.95, 1.08), 3)
            saturation = round(random.uniform(0.95, 1.15), 3)
            video_filters.append(
                f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
            )

        speed_factor = 1.0
        if speed_change:
            speed_factor = round(random.uniform(0.97, 1.05), 3)
            video_filters.append(f"setpts=PTS/{speed_factor}")

        if fade:
            fade_duration = 0.4
            effective_duration = duration / speed_factor if speed_factor else duration
            fade_out_start = max(effective_duration - fade_duration, 0)
            video_filters.append(f"fade=t=in:st=0:d={fade_duration}")
            video_filters.append(f"fade=t=out:st={fade_out_start}:d={fade_duration}")

        video_filter_str = ",".join(video_filters) if video_filters else "null"
        logger.info("Filtros de vídeo montados: %s", video_filter_str)

        # Otimização: em vez de rodar um ffmpeg separado só para o corte
        # (copy) e depois reencodar o resultado num segundo processo, o
        # corte é aplicado diretamente no comando principal via "-ss"/"-t"
        # antes do encode final. Isso elimina uma chamada inteira de
        # subprocesso e um arquivo temporário, reduzindo tempo total de
        # processamento e pontos onde o pipeline poderia travar.
        cmd = ["ffmpeg", "-y"]

        if trim_duration is not None:
            cmd += ["-ss", str(start_cut)]

        cmd += ["-i", input_path]

        if trim_duration is not None:
            cmd += ["-t", str(trim_duration)]

        if remove_audio:
            cmd.append("-an")
        elif speed_change:
            cmd += ["-af", f"atempo={_clamp_atempo(speed_factor)}"]

        cmd += [
            "-vf", video_filter_str,
            "-c:v", "libx264",
            # veryfast (em vez de medium) reduz bastante o tempo de encode
            # com impacto pequeno no tamanho/qualidade final.
            "-preset", "veryfast",
            "-crf", "26",
            "-threads", "0",
            # faststart move os metadados para o início do arquivo,
            # permitindo que o frontend comece a reproduzir mais rápido.
            "-movflags", "+faststart",
        ]

        if not remove_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k"]

        cmd.append(output_path)

        _run_ffmpeg(cmd, step_name="encode-final")

        total_elapsed = time.monotonic() - overall_start
        logger.info(
            "Processamento concluído com sucesso em %.1fs: %s",
            total_elapsed, output_path,
        )
        return output_path

    except VideoProcessingError:
        # Já logado com detalhes no ponto de origem; apenas repropaga para
        # o endpoint tratar e responder ao frontend com mensagem clara.
        raise
    except Exception as exc:
        logger.exception(
            "Erro inesperado durante o processamento do vídeo (input=%s)",
            input_path,
        )
        raise VideoProcessingError(
            f"Erro inesperado ao processar o vídeo: {exc}"
        ) from exc
    finally:
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    logger.info("Arquivo temporário removido: %s", temp_file)
            except Exception as exc:
                logger.warning(
                    "Falha ao limpar arquivo temporário %s: %s", temp_file, exc
                )
