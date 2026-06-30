#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
publicar_meme.py — Sistema Automatizado de Curación y Publicación de Memes
=============================================================================
Descripción:
    Este script descubre, descarga, procesa y publica automáticamente los
    mejores memes virales de TikTok en español hacia Instagram Reels e
    Instagram Stories, usando la API oficial de Meta Graph.

    ARQUITECTURA 100% GRATUITA:
    ┌─ Scraping TikTok ──────────────────────────────────────────────────────┐
    │  yt-dlp (gratuito, código abierto) para:                               │
    │    • Obtener metadatos de hashtags (vistas, likes, comentarios)        │
    │    • Descargar videos sin marca de agua en alta calidad                │
    └────────────────────────────────────────────────────────────────────────┘
    ┌─ Hosting de medios ────────────────────────────────────────────────────┐
    │  rupload.facebook.com (API oficial de Meta, completamente gratuita):   │
    │    • Subida binaria directa a los servidores de Meta                   │
    │    • Sin necesidad de ningún hosting externo (ni Cloudinary, ni S3)    │
    │    • Proceso: crear contenedor → subir binario → publicar              │
    └────────────────────────────────────────────────────────────────────────┘

Flujo de trabajo:
    1. Extrae metadatos de los mejores videos de hashtags de humor español
    2. Calcula una puntuación viral y selecciona el TOP 5
    3. Descarga los videos sin marca de agua usando yt-dlp
    4. Sanitiza los archivos con FFmpeg (re-encode + hash-breaking)
    5. Sube los archivos directamente a Meta vía rupload.facebook.com
    6. Publica el #1 inmediatamente como Instagram Reel
    7. Publica los 4 restantes como Instagram Stories distribuidas en 24h

Dependencias:
    pip install yt-dlp requests pytz

    Binarios del sistema (instalados en el workflow de GitHub Actions):
    - ffmpeg

Autor: Sistema de automatización generado por IA
Versión: 2.0.0 — Stack 100% Gratuito
=============================================================================
"""

import os
import sys

# Forzar UTF-8 en la consola para evitar errores de codificación (UnicodeEncodeError) en Windows
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

import time
import json
import math
import random
import logging
import hashlib
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("publicar_meme.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONSTANTES Y CONFIGURACIÓN GLOBAL
# ---------------------------------------------------------------------------

# Búsquedas de palabras clave para encontrar memes de España (en orden de prioridad)
BUSQUEDAS_OBJETIVO = [
    "meme españa",
    "meme español",
    "meme",
    "memes",
    "memes mundial",
    "shitpost españa",
    "shitpost español",
    "memes xokas",
    "memes chiringuito",
]

# Región objetivo para filtrar resultados
REGION_OBJETIVO = {"ES"}

# Cuentas de TikTok excluidas (no se seleccionarán sus videos)
CUENTAS_EXCLUIDAS = {"failets", "lobostroy"}

# Al menos uno de estos términos debe aparecer en la descripción del vídeo.
# Garantiza que el contenido sea efectivamente un meme o humor, y no un vídeo
# cualquiera que solo mencione "españa" junto a la palabra meme de pasada.
TAGS_MEME_REQUERIDOS = {
    "meme", "memes", "humor", "gracioso", "momazo", "shitpost",
}

# Número de videos a seleccionar para publicación
TOP_N_VIDEOS = 5

# Dimensiones estándar para formato vertical Instagram (9:16)
ANCHO_VIDEO = 1080
ALTO_VIDEO = 1920

# Ventana de tiempo para considerar videos "recientes"
HORAS_MAXIMO_ANTIGUEDAD = 24

# Intervalo entre publicaciones de Stories: ajustado a 75 minutos (4500s)
# para distribuir las 4 Stories a lo largo de las 5 horas (300 min) de duración máxima
# segura de la sesión de GitHub Actions (timeout-minutes configurado en 330 min).
INTERVALO_STORIES_SEGUNDOS = 75 * 60

# Tiempo máximo de espera para que el contenedor de Meta esté listo
TIMEOUT_CONTENEDOR_META = 300   # 5 minutos
INTERVALO_POLLING_META = 15     # Verificar cada 15 segundos

# Versión de la API de Meta Graph a utilizar
META_API_VERSION = "v20.0"

# Host para subida de videos directa a Meta
META_RUPLOAD_HOST = "https://rupload.facebook.com"
META_GRAPH_BASE = f"https://graph.facebook.com/{META_API_VERSION}"

ZONA_HORARIA_UTC = timezone.utc


# ---------------------------------------------------------------------------
# CARGA DE CREDENCIALES DESDE VARIABLES DE ENTORNO (GitHub Secrets)
# ---------------------------------------------------------------------------

def cargar_credenciales() -> dict:
    """
    Carga de forma segura las credenciales desde variables de entorno.
    En producción, estas se configuran como GitHub Secrets.

    IMPORTANTE: Esta versión NO requiere claves de API de terceros de pago.
    Solo se necesitan las credenciales oficiales de Meta.

    Retorna:
        dict: Diccionario con todas las credenciales necesarias.

    Lanza:
        SystemExit: Si alguna credencial obligatoria no está definida.
    """
    credenciales = {
        # ── Meta Graph API (gratuito, cuenta oficial) ──────────────────────
        # Token de acceso de larga duración (válido 60 días)
        "META_ACCESS_TOKEN": os.environ.get("META_ACCESS_TOKEN"),
        # ID numérico de la cuenta de Instagram Business o Creator
        "INSTAGRAM_ACCOUNT_ID": os.environ.get("INSTAGRAM_ACCOUNT_ID"),
        # App ID de Meta (necesario para la subida resumible)
        "META_APP_ID": os.environ.get("META_APP_ID"),
    }

    # Verificar que todas las credenciales obligatorias estén presentes
    faltantes = [k for k, v in credenciales.items() if not v]

    if faltantes:
        logger.error(
            "❌ Credenciales obligatorias no encontradas: %s",
            ", ".join(faltantes)
        )
        logger.error(
            "   Configúralas en GitHub → Settings → Secrets and variables → Actions"
        )
        sys.exit(1)

    logger.info("✅ Credenciales cargadas. Stack: 100%% gratuito (yt-dlp + Meta API directa)")
    return credenciales


# ---------------------------------------------------------------------------
# MÓDULO 1: DESCUBRIMIENTO Y SCRAPING DE TIKTOK (via yt-dlp — GRATUITO)
# ---------------------------------------------------------------------------

class ClienteTikTok:
    """
    Cliente de scraping de TikTok usando yt-dlp (100% gratuito y open source).

    yt-dlp es una herramienta de línea de comandos y librería Python que
    extrae metadatos y descarga videos de TikTok directamente de sus APIs
    internas sin requerir claves de API ni cuentas de terceros.

    Limitaciones conocidas:
    - TikTok puede bloquear peticiones desde IPs de datacenters conocidos.
      Si ocurre, añadir cookies de sesión (ver parámetro 'cookies_file').
    - El scraping de hashtags puede retornar un número variable de resultados
      dependiendo de las restricciones geográficas de TikTok.
    """

    def __init__(self, cookies_file: Optional[str] = None):
        """
        Inicializa el cliente de TikTok.

        Args:
            cookies_file: Ruta opcional a un archivo Netscape cookies.txt
                          exportado desde un navegador con sesión activa en TikTok.
                          Ayuda a evitar bloqueos de IP en entornos cloud.
        """
        self.cookies_file = cookies_file

        # Verificar que yt-dlp esté instalado
        try:
            resultado = subprocess.run(
                ["yt-dlp", "--version"],
                capture_output=True, text=True, timeout=10
            )
            logger.info("✅ yt-dlp versión: %s", resultado.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.error("❌ yt-dlp no encontrado. Instalar con: pip install yt-dlp")
            sys.exit(1)

    def _construir_argumentos_base(self) -> list[str]:
        """
        Construye la lista de argumentos comunes para todas las llamadas a yt-dlp.

        Retorna:
            list[str]: Lista de argumentos base de yt-dlp.
        """
        args = [
            "yt-dlp",
            "--quiet",                    # Suprime output normal (solo errores)
            "--no-warnings",              # Suprime avisos no críticos
            "--dump-json",                # Retorna metadatos en JSON por stdout
            "--no-download",              # Solo metadatos, sin descargar ahora
            "--user-agent",               # Simular user-agent de navegador real
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--sleep-interval", "2",      # Pausa de 2s entre peticiones (evita rate limiting)
            "--max-sleep-interval", "5",  # Pausa máxima de 5s (aleatoria)
        ]

        # Añadir cookies si están disponibles (mejora la evasión de bloqueos)
        if self.cookies_file and Path(self.cookies_file).exists():
            args.extend(["--cookies", self.cookies_file])
            logger.info("   🍪 Usando cookies de sesión de TikTok")

        return args

    def buscar_videos_por_keywords(
        self,
        keywords: str,
        max_paginas: int = 10,
        publish_time: int = 1
    ) -> list[dict]:
        """
        Busca vídeos en TikTok usando palabras clave (ej: "meme españa") paginando
        hasta max_paginas páginas de resultados.

        Args:
            keywords: Términos de búsqueda (ej: "meme españa").
            max_paginas: Número máximo de páginas a consultar (30 vídeos/página).
            publish_time: Ventana de tiempo de la API (1=últimas 24h, 7=última semana).

        Retorna:
            list[dict]: Lista de metadatos crudos de TikWM.
        """
        url_api = "https://www.tikwm.com/api/feed/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        logger.info("🔍 Buscando: '%s' (hasta %d páginas)...", keywords, max_paginas)

        todos = []
        cursor = 0
        for pagina in range(max_paginas):
            payload = {
                "keywords": keywords,
                "count": 30,
                "cursor": cursor,
                "publish_time": publish_time,
            }
            try:
                resp = requests.post(url_api, data=payload, headers=headers, timeout=20)
                if resp.status_code != 200:
                    break
                datos = resp.json()
                if datos.get("code") == 0 and "data" in datos:
                    videos = datos["data"].get("videos", [])
                    todos.extend(videos)
                    has_more = datos["data"].get("hasMore")
                    next_cursor = datos["data"].get("cursor")
                    if not has_more or not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                else:
                    break
            except Exception as e:
                logger.debug("Error buscando '%s' pág %d: %s", keywords, pagina + 1, e)
                break
            time.sleep(1)

        logger.info("   → %d vídeos obtenidos para '%s'", len(todos), keywords)
        return todos

    def descargar_video_sin_watermark(
        self,
        url_video: str,
        ruta_destino: Path
    ) -> bool:
        """
        Descarga un video de TikTok sin marca de agua usando yt-dlp.

        yt-dlp selecciona automáticamente el formato de mayor calidad sin
        watermark disponible. TikTok sirve estos videos en el formato
        'download_addr' que no incluye el logo de TikTok.

        Args:
            url_video: URL completa del video en TikTok.
            ruta_destino: Ruta local donde guardar el video descargado.

        Retorna:
            bool: True si la descarga fue exitosa.
        """
        logger.info("⬇️  Descargando video sin watermark: %s", url_video[:60])

        comando = [
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            # Formato: preferir el formato sin watermark de mayor resolución
            # TikTok etiqueta los formatos sin watermark con IDs específicos
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "--no-playlist",              # No descargar listas completas
            "--merge-output-format", "mp4",  # Formato de salida siempre MP4
            "--output", str(ruta_destino),   # Ruta de destino
            "--no-part",                  # No crear archivos .part temporales
            "--user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "--sleep-interval", "1",
            url_video,
        ]

        # Añadir cookies si están disponibles
        if self.cookies_file and Path(self.cookies_file).exists():
            comando.extend(["--cookies", self.cookies_file])

        try:
            resultado = subprocess.run(
                comando,
                capture_output=True,
                text=True,
                timeout=180,  # 3 minutos máximo por video
            )

            if ruta_destino.exists() and ruta_destino.stat().st_size > 10000:
                tamaño_mb = ruta_destino.stat().st_size / (1024 * 1024)
                logger.info("   ✅ Descargado exitosamente: %.2f MB", tamaño_mb)
                return True
            else:
                logger.error(
                    "   ❌ Descarga fallida (código: %d): %s",
                    resultado.returncode,
                    resultado.stderr[:300]
                )
                return False

        except subprocess.TimeoutExpired:
            logger.error("   ❌ Timeout durante la descarga.")
            return False


def normalizar_metadatos_ytdlp(video_raw: dict) -> dict:
    """
    Normaliza los metadatos retornados por yt-dlp al formato esperado por el sistema.

    yt-dlp usa nombres de campo estándar independientes de la plataforma, por lo
    que necesitamos mapearlos al esquema interno del sistema.

    Args:
        video_raw: Diccionario de metadatos crudo de yt-dlp.

    Retorna:
        dict: Metadatos normalizados con los campos esperados por el sistema.
    """
    return {
        # Identificadores
        "id": video_raw.get("id", ""),
        "webpage_url": video_raw.get("webpage_url", ""),

        # Métricas de engagement (yt-dlp usa estos nombres estándar)
        "view_count":    int(video_raw.get("view_count", 0) or 0),
        "like_count":    int(video_raw.get("like_count", 0) or 0),
        "comment_count": int(video_raw.get("comment_count", 0) or 0),
        "share_count":   int(video_raw.get("repost_count", 0) or 0),

        # Temporal
        "timestamp": int(video_raw.get("timestamp", 0) or 0),

        # Contenido
        "duration":     float(video_raw.get("duration", 0) or 0),
        "description":  video_raw.get("description", ""),
        "uploader":     video_raw.get("uploader", ""),

        # Conservar datos originales para referencia
        "_raw": video_raw,
    }


def normalizar_metadatos_tikwm(video_raw: dict) -> dict:
    """
    Normaliza los metadatos retornados por la API de TikWM al formato esperado por el sistema.
    """
    video_id = video_raw.get("video_id", "")
    author_unique_id = video_raw.get("author", {}).get("unique_id", "")
    
    # Construimos la URL canónica de TikTok
    webpage_url = f"https://www.tiktok.com/@{author_unique_id}/video/{video_id}" if video_id and author_unique_id else ""
    
    return {
        "id": video_id,
        "webpage_url": webpage_url,
        "view_count":    int(video_raw.get("play_count", 0) or 0),
        "like_count":    int(video_raw.get("digg_count", 0) or 0),
        "comment_count": int(video_raw.get("comment_count", 0) or 0),
        "share_count":   int(video_raw.get("share_count", 0) or 0),
        "timestamp": int(video_raw.get("create_time", 0) or 0),
        "duration":     float(video_raw.get("duration", 0) or 0),
        "description":  video_raw.get("title", ""),
        "uploader":     author_unique_id,
        "_raw": video_raw,
    }


def calcular_puntuacion_viral(video: dict) -> float:
    """Calcula la Puntuacion Viral ponderando compartidos, likes, comentarios y vistas.

    VS = log10(vistas+1)*30 + log10(likes+1)*25 + log10(compartidos+1)*25
       + log10(comentarios+1)*15 + ratio_engagement*5 + bonus_recencia(0-5)
    """
    vistas      = video.get("view_count", 0)
    likes       = video.get("like_count", 0)
    comentarios = video.get("comment_count", 0)
    compartidos = video.get("share_count", 0)

    pts_vistas      = math.log10(vistas + 1) * 30
    pts_likes       = math.log10(likes + 1) * 25
    pts_compartidos = math.log10(compartidos + 1) * 25
    pts_comentarios = math.log10(comentarios + 1) * 15

    ratio = (likes + compartidos) / max(vistas, 1)
    pts_ratio = min(ratio * 50, 5.0)

    ahora = int(datetime.now(ZONA_HORARIA_UTC).timestamp())
    horas = (ahora - video.get("timestamp", 0)) / 3600
    if horas <= 6:
        bonus_recencia = 5.0
    elif horas <= 12:
        bonus_recencia = 3.0
    elif horas <= 24:
        bonus_recencia = 1.0
    else:
        bonus_recencia = 0.0

    return round(pts_vistas + pts_likes + pts_compartidos + pts_comentarios + pts_ratio + bonus_recencia, 4)



def es_video_valido(video: dict) -> bool:
    """
    Verifica que un video cumple los criterios mínimos de calidad.

    Filtros aplicados:
    1. Publicado dentro de las últimas 24 horas.
    2. Duración entre 5 y 90 segundos (formato Reels/Stories).
    3. Al menos 1,000 reproducciones (tiene tracción).
    4. Tiene URL de página web accesible.

    Args:
        video: Metadatos normalizados del video.

    Retorna:
        bool: True si el video pasa todos los filtros.
    """
    ahora = int(datetime.now(ZONA_HORARIA_UTC).timestamp())
    horas = (ahora - video.get("timestamp", 0)) / 3600

    if horas > HORAS_MAXIMO_ANTIGUEDAD:
        return False

    duracion = video.get("duration", 0)
    if not (5 <= duracion <= 90):
        return False

    if video.get("view_count", 0) < 1000:
        return False

    if not video.get("webpage_url"):
        return False

    return True


def descubrir_mejores_videos(cliente: ClienteTikTok) -> list[dict]:
    """
    Busca vídeos de memes de España usando palabras clave ("meme españa", etc.),
    recorriendo todas las búsquedas configuradas y recopilando todos los candidatos
    válidos posibles para maximizar la muestra y elegir el mejor TOP N.

    Criterios de validez:
    - Región: ES (España)
    - Antigüedad: < 24 horas
    - Duración: entre 5 y 90 segundos

    Selección final: TOP_N_VIDEOS ordenados por puntuación viral.

    Args:
        cliente: Instancia del cliente de TikTok.

    Retorna:
        list[dict]: TOP_N_VIDEOS videos ordenados por puntuación viral descendente.
    """
    ahora = int(datetime.now(ZONA_HORARIA_UTC).timestamp())
    candidatos = []
    ids_vistos = set()

    for keywords in BUSQUEDAS_OBJETIVO:
        videos_raw = cliente.buscar_videos_por_keywords(keywords, max_paginas=10, publish_time=1)

        for v_raw in videos_raw:
            video = normalizar_metadatos_tikwm(v_raw)
            video_id = video.get("id", "")

            if not video_id or video_id in ids_vistos:
                continue

            # Filtro de región
            region = v_raw.get("region", "").upper()
            if region not in REGION_OBJETIVO:
                continue

            # Filtro de cuentas excluidas (evitar contenido no deseado)
            uploader = video.get("uploader", "").lower()
            if uploader in CUENTAS_EXCLUIDAS:
                continue

            # Filtro de antigüedad: últimas 24h
            horas = (ahora - video.get("timestamp", 0)) / 3600
            if horas > 24.0:
                continue

            # Filtro de duración: 5 – 90 segundos
            if not (5 <= video.get("duration", 0) <= 90):
                continue

            # Filtro de contenido: la descripción debe contener al menos un hashtag
            # de meme/humor EXACTO (ej: #meme sí, #memesespaña no).
            desc = v_raw.get("title", "").lower()
            # Extraemos los tokens del texto separando por cualquier carácter no alfanumérico
            # (excepto #) para obtener palabras como "#meme" sin trailing punctuation.
            tokens = set(re.sub(r"[^\w#]", " ", desc).split())
            if not any(f"#{tag}" in tokens for tag in TAGS_MEME_REQUERIDOS):
                continue

            ids_vistos.add(video_id)
            video["region"] = region
            video["_busqueda_origen"] = keywords
            video["_puntuacion_viral"] = calcular_puntuacion_viral(video)
            candidatos.append(video)

        time.sleep(1.5)

    if not candidatos:
        logger.warning("⚠️ No se encontraron vídeos de España en las últimas 24h. Fin limpio.")
        return []

    candidatos.sort(key=lambda v: v["_puntuacion_viral"], reverse=True)
    seleccionados = candidatos[:TOP_N_VIDEOS]

    ahora_log = int(datetime.now(ZONA_HORARIA_UTC).timestamp())
    logger.info(
        "\n🏆 TOP %d seleccionados de %d candidatos:",
        len(seleccionados), len(candidatos)
    )
    for i, v in enumerate(seleccionados, 1):
        horas = (ahora_log - v.get("timestamp", 0)) / 3600
        logger.info(
            "   %d. VS=%.2f | Vistas=%d | Likes=%d | Compartidos=%d | Edad=%.1fh | busq='%s' | @%s | %s",
            i,
            v["_puntuacion_viral"],
            v["view_count"],
            v["like_count"],
            v["share_count"],
            horas,
            v.get("_busqueda_origen", "?"),
            v["uploader"],
            v["webpage_url"],
        )

    return seleccionados


# ---------------------------------------------------------------------------
# MÓDULO 2: DESCARGA Y SANITIZACIÓN DE VIDEOS CON FFMPEG
# ---------------------------------------------------------------------------

def sanitizar_video_con_ffmpeg(
    ruta_entrada: Path,
    ruta_salida: Path,
    indice: int = 0,
) -> bool:
    """
    Procesa el video con FFmpeg para sanitizarlo y alterar su hash digital.

    Técnicas aplicadas para evadir la detección de contenido duplicado:
    1. Re-encode completo H.264: genera un bitstream totalmente nuevo.
    2. Padding 9:16 (1080x1920): formato vertical estándar de Instagram.
    3. Variación aleatoria de contraste/saturación/brillo: ±0.3-1.0%.
    4. Píxel firma invisible en posición aleatoria: 1px modificado.
    5. Eliminación total de metadatos (-map_metadata -1).
    6. faststart: optimización para streaming desde el inicio del archivo.

    Args:
        ruta_entrada: Video descargado sin watermark.
        ruta_salida: Destino del video procesado y sanitizado.
        indice: Número del video (para logs).

    Retorna:
        bool: True si el procesamiento fue exitoso.
    """
    # Variaciones aleatorias únicas para cada video
    contraste  = 1.0 + random.uniform(0.003, 0.010)
    saturacion = 1.0 + random.uniform(0.003, 0.010)
    brillo     = random.uniform(-0.005, 0.005)
    # Posición aleatoria del píxel "firma" (prácticamente invisible)
    px = random.randint(5, 50)
    py = random.randint(5, 50)

    # Pipeline de filtros FFmpeg
    filtro_video = (
        # 1. Escalar al tamaño objetivo manteniendo aspecto original
        f"scale={ANCHO_VIDEO}:{ALTO_VIDEO}:force_original_aspect_ratio=decrease,"
        # 2. Rellenar el espacio restante con barras negras (letterbox/pillarbox)
        f"pad={ANCHO_VIDEO}:{ALTO_VIDEO}:(ow-iw)/2:(oh-ih)/2:black,"
        # 3. Establecer Sample Aspect Ratio cuadrado (requerido por Instagram)
        f"setsar=1,"
        # 4. Ajuste sutil de color para alterar el perceptual hash (pHash)
        f"eq=contrast={contraste:.6f}:brightness={brillo:.6f}:saturation={saturacion:.6f},"
        # 5. Píxel firma semi-invisible en posición aleatoria
        f"drawbox=x={px}:y={py}:w=1:h=1:color=black@0.01:t=1"
    )

    comando_ffmpeg = [
        "ffmpeg",
        "-y",                         # Sobrescribir sin confirmación
        "-i", str(ruta_entrada),      # Entrada
        "-vf", filtro_video,          # Filtros de video
        "-c:v", "libx264",            # Códec de video: H.264
        "-preset", "medium",          # Balance velocidad/calidad
        "-crf", "23",                 # Calidad visual (0=perfecto, 51=peor)
        "-c:a", "aac",                # Códec de audio: AAC
        "-b:a", "128k",               # Bitrate de audio
        "-ar", "44100",               # Frecuencia de muestreo
        "-map_metadata", "-1",        # ★ ELIMINAR TODOS LOS METADATOS ORIGINALES
        "-movflags", "+faststart",    # Optimizar para streaming web (moov al inicio)
        "-pix_fmt", "yuv420p",        # Formato de píxel: máxima compatibilidad
        str(ruta_salida),             # Salida
    ]

    logger.info(
        "🎬 Sanitizando video %d (contraste=%.4f, sat=%.4f, píxel firma=[%d,%d])...",
        indice + 1, contraste, saturacion, px, py
    )

    try:
        resultado = subprocess.run(
            comando_ffmpeg,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if resultado.returncode != 0:
            logger.error(
                "   ❌ FFmpeg falló (código %d):\n%s",
                resultado.returncode,
                resultado.stderr[-600:]
            )
            return False

        if not ruta_salida.exists() or ruta_salida.stat().st_size < 10000:
            logger.error("   ❌ Archivo de salida inválido o vacío.")
            return False

        # Mostrar diferencia de hashes para confirmar que el archivo cambió
        hash_entrada = hashlib.md5(ruta_entrada.read_bytes()).hexdigest()[:8]
        hash_salida  = hashlib.md5(ruta_salida.read_bytes()).hexdigest()[:8]
        tamaño_mb    = ruta_salida.stat().st_size / (1024 * 1024)

        logger.info(
            "   ✅ %.2f MB | Hash original: %s... → Hash nuevo: %s...",
            tamaño_mb, hash_entrada, hash_salida
        )
        return True

    except subprocess.TimeoutExpired:
        logger.error("   ❌ FFmpeg excedió el tiempo límite (5 min).")
        return False
    except FileNotFoundError:
        logger.error("   ❌ FFmpeg no está instalado. Verificar el paso de setup en el workflow.")
        sys.exit(1)


def descargar_y_procesar_videos(
    videos: list[dict],
    cliente_tiktok: ClienteTikTok,
    directorio_trabajo: Path,
) -> list[dict]:
    """
    Orquesta la descarga (yt-dlp) y sanitización (FFmpeg) de todos los videos.

    Args:
        videos: Lista de metadatos del TOP N.
        cliente_tiktok: Cliente de TikTok basado en yt-dlp.
        directorio_trabajo: Directorio temporal para archivos intermedios.

    Retorna:
        list[dict]: Videos con rutas de archivos procesados añadidas.
    """
    videos_procesados = []

    for i, video in enumerate(videos):
        video_id = video.get("id", f"video_{i}")
        url_video = video.get("webpage_url", "")

        logger.info(
            "\n📥 Procesando video %d/%d (ID: %s)...",
            i + 1, len(videos), video_id
        )

        if not url_video:
            logger.warning("   ⚠️  Sin URL de página web. Saltando.")
            continue

        ruta_original  = directorio_trabajo / f"original_{i}_{video_id[:20]}.mp4"
        ruta_procesado = directorio_trabajo / f"procesado_{i}_{video_id[:20]}.mp4"

        # Paso 1: Descargar sin watermark con yt-dlp
        if not cliente_tiktok.descargar_video_sin_watermark(url_video, ruta_original):
            continue

        # Paso 2: Sanitizar con FFmpeg
        if not sanitizar_video_con_ffmpeg(ruta_original, ruta_procesado, i):
            continue

        # Eliminar el archivo original para liberar espacio en el runner
        ruta_original.unlink(missing_ok=True)

        video["_ruta_local"] = ruta_procesado
        video["_tamaño_bytes"] = ruta_procesado.stat().st_size
        videos_procesados.append(video)

        # Pausa breve entre videos para no saturar la red del runner
        time.sleep(1)

    logger.info(
        "\n✅ %d/%d videos procesados y listos para publicar.",
        len(videos_procesados), len(videos)
    )
    return videos_procesados


# ---------------------------------------------------------------------------
# MÓDULO 3: SUBIDA DIRECTA A META VÍA RUPLOAD (SIN HOSTING EXTERNO)
# ---------------------------------------------------------------------------

class SubidaResumibleMeta:
    """
    Implementa la subida binaria directa de videos a los servidores de Meta
    mediante el endpoint rupload.facebook.com.

    Este flujo elimina completamente la necesidad de hosting externo:
    - NO se necesita Cloudinary, S3, Google Drive ni ningún otro servicio.
    - Los videos se suben directamente desde el runner de GitHub Actions
      a los servidores de Meta con una sola llamada HTTP.

    Proceso para Reels (upload_type=resumable):
        1. POST /{ig-user-id}/media con media_type=REELS y upload_type=resumable
           → Retorna: { id: "<contenedor_id>", uri: "https://rupload.facebook.com/..." }
        2. POST a rupload.facebook.com con el binario del video en el cuerpo
           → Retorna: { success: true }
        3. Polling del estado del contenedor hasta FINISHED
        4. POST /{ig-user-id}/media_publish con el creation_id
           → Retorna: { id: "<media_id>" }

    Proceso para Stories (no soporta upload_type=resumable):
        Las Stories usan la API de hosting interno de Meta via video_url.
        Se sube el video a Meta usando el endpoint de Reels, se obtiene la URL
        del contenedor ya procesado, y se usa para crear el contenedor de Story.
        Alternativa implementada: subida directa del binario usando el mismo
        endpoint de rupload y reutilizando el contenedor.
    """

    RUPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"

    def __init__(self, access_token: str, instagram_account_id: str):
        """
        Inicializa el cliente de subida resumible de Meta.

        Args:
            access_token: Token de acceso de larga duración de Meta.
            instagram_account_id: ID de la cuenta de Instagram Business/Creator.
        """
        self.token = access_token
        self.account_id = instagram_account_id
        self.sesion = requests.Session()
        # Headers de autenticación OAuth estándar de Meta
        self.sesion.headers.update({
            "Authorization": f"OAuth {self.token}",
        })

    def _graph_post(self, endpoint: str, datos: dict) -> Optional[dict]:
        """
        Realiza una petición POST a la API de Meta Graph con manejo de errores.

        Args:
            endpoint: Ruta del endpoint (ej: '/{id}/media').
            datos: Datos del cuerpo de la petición.

        Retorna:
            dict | None: Respuesta JSON de la API.
        """
        url = f"{META_GRAPH_BASE}{endpoint}"
        datos["access_token"] = self.token

        try:
            resp = requests.post(url, data=datos, timeout=60)
            datos_resp = resp.json()

            if "error" in datos_resp:
                err = datos_resp["error"]
                logger.error(
                    "❌ Error Meta Graph API [%s] código %s: %s",
                    err.get("type", "?"),
                    err.get("code", "?"),
                    err.get("message", "?"),
                )
                return None

            return datos_resp

        except requests.exceptions.RequestException as e:
            logger.error("❌ Error de red en Graph API: %s", e)
            return None

    def _graph_get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """
        Realiza una petición GET a la API de Meta Graph.

        Args:
            endpoint: Ruta del endpoint.
            params: Parámetros de query string adicionales.

        Retorna:
            dict | None: Respuesta JSON de la API.
        """
        url = f"{META_GRAPH_BASE}{endpoint}"
        query = {"access_token": self.token}
        if params:
            query.update(params)

        try:
            resp = requests.get(url, params=query, timeout=30)
            datos_resp = resp.json()

            if "error" in datos_resp:
                err = datos_resp["error"]
                logger.error(
                    "❌ Error Graph GET [%s]: %s",
                    err.get("type", "?"),
                    err.get("message", "?"),
                )
                return None

            return datos_resp

        except requests.exceptions.RequestException as e:
            logger.error("❌ Error de red en Graph GET: %s", e)
            return None

    def crear_contenedor_resumible(
        self,
        media_type: str,
        caption: str = "",
    ) -> Optional[tuple[str, str]]:
        """
        Paso 1: Crea un contenedor de medios con upload_type=resumable.

        Este tipo de contenedor no requiere URL pública: Meta devuelve
        una URI de rupload.facebook.com a la que se sube el binario.

        Args:
            media_type: 'REELS' o 'STORIES'.
            caption: Descripción/caption (solo para REELS).

        Retorna:
            tuple[str, str] | None: (contenedor_id, uri_de_rupload) o None.
        """
        logger.info("📦 Creando contenedor %s (upload_type=resumable)...", media_type)

        datos: dict = {
            "media_type": media_type,
            "upload_type": "resumable",
        }

        if media_type == "REELS" and caption:
            datos["caption"] = caption
            datos["share_to_feed"] = "true"

        resp = self._graph_post(f"/{self.account_id}/media", datos)

        if resp and "id" in resp and "uri" in resp:
            contenedor_id = resp["id"]
            uri_rupload   = resp["uri"]
            logger.info(
                "   ✅ Contenedor creado: %s | URI: %s...",
                contenedor_id, uri_rupload[:60]
            )
            return contenedor_id, uri_rupload

        logger.error("   ❌ No se pudo crear el contenedor %s.", media_type)
        return None

    def subir_binario_a_rupload(
        self,
        uri_rupload: str,
        ruta_video: Path,
    ) -> bool:
        """
        Paso 2: Sube el binario del video directamente a rupload.facebook.com.

        Esta es la parte clave que elimina la necesidad de hosting externo.
        El video se lee desde el disco del runner y se envía como cuerpo
        binario de una petición HTTP POST a Meta.

        Args:
            uri_rupload: URI devuelta por el paso de creación del contenedor.
            ruta_video: Ruta local al archivo de video procesado.

        Retorna:
            bool: True si la subida fue exitosa.
        """
        tamaño_bytes = ruta_video.stat().st_size
        tamaño_mb    = tamaño_bytes / (1024 * 1024)

        logger.info(
            "📤 Subiendo binario a rupload.facebook.com (%.2f MB)...", tamaño_mb
        )

        try:
            with open(ruta_video, "rb") as archivo:
                resp = requests.post(
                    uri_rupload,
                    headers={
                        "Authorization": f"OAuth {self.token}",
                        "offset": "0",                    # Byte de inicio (0 = desde el principio)
                        "file_size": str(tamaño_bytes),  # Tamaño total del archivo en bytes
                        "Content-Type": "application/octet-stream",
                    },
                    data=archivo,   # Stream del archivo binario
                    timeout=600,    # 10 minutos: suficiente para archivos grandes
                )

            datos = resp.json()

            if datos.get("success") is True:
                logger.info("   ✅ Subida exitosa a rupload.facebook.com")
                return True
            else:
                logger.error(
                    "   ❌ Error en rupload: %s",
                    json.dumps(datos)[:300]
                )
                return False

        except requests.exceptions.RequestException as e:
            logger.error("   ❌ Error de red durante la subida: %s", e)
            return False
        except OSError as e:
            logger.error("   ❌ Error al leer el archivo: %s", e)
            return False

    def esperar_contenedor_listo(self, contenedor_id: str) -> bool:
        """
        Polling del estado del contenedor hasta que esté FINISHED.

        Meta procesa el video de forma asíncrona tras recibirlo. Este método
        verifica el estado periódicamente hasta que el procesamiento termine.

        Estados posibles:
        - IN_PROGRESS: Procesando (continuar esperando)
        - FINISHED: Listo para publicar
        - ERROR: Error irrecuperable
        - EXPIRED: Expiró sin publicarse (válido 24h)

        Args:
            contenedor_id: ID del contenedor a monitorear.

        Retorna:
            bool: True si llegó al estado FINISHED.
        """
        tiempo_inicio = time.time()
        intento = 0

        while time.time() - tiempo_inicio < TIMEOUT_CONTENEDOR_META:
            intento += 1
            resp = self._graph_get(
                f"/{contenedor_id}",
                params={"fields": "status_code,status"}
            )

            if not resp:
                logger.warning("   ⚠️  Sin respuesta en el intento %d. Reintentando...", intento)
                time.sleep(INTERVALO_POLLING_META)
                continue

            estado = resp.get("status_code", "UNKNOWN")
            seg_transcurridos = int(time.time() - tiempo_inicio)
            logger.info(
                "   ⏳ Intento %d | Estado: %-12s | %ds transcurridos",
                intento, estado, seg_transcurridos
            )

            if estado == "FINISHED":
                logger.info("   ✅ Contenedor listo para publicar.")
                return True
            elif estado in ("ERROR", "EXPIRED"):
                logger.error("   ❌ Estado irrecuperable: %s", estado)
                return False

            time.sleep(INTERVALO_POLLING_META)

        logger.error(
            "   ❌ Timeout: el contenedor no procesó en %ds.", TIMEOUT_CONTENEDOR_META
        )
        return False

    def publicar_contenedor(self, contenedor_id: str) -> Optional[str]:
        """
        Paso 3: Publica el contenedor ya procesado en Instagram.

        Solo puede llamarse después de que el contenedor esté en FINISHED.

        Args:
            contenedor_id: ID del contenedor a publicar.

        Retorna:
            str | None: Media ID del post publicado, o None si falla.
        """
        logger.info("🚀 Publicando contenedor %s...", contenedor_id)

        resp = self._graph_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": contenedor_id},
        )

        if resp and "id" in resp:
            media_id = resp["id"]
            logger.info("   ✅ ¡Publicado! Media ID: %s", media_id)
            return media_id

        logger.error("   ❌ Error al publicar el contenedor.")
        return None

    def publicar_reel_desde_archivo(
        self,
        ruta_video: Path,
        caption: str = "",
    ) -> Optional[str]:
        """
        Flujo completo para publicar un Reel directamente desde archivo local.

        No requiere ningún hosting externo. El video va directamente
        del disco del runner a los servidores de Meta.

        Pasos:
            1. Crear contenedor con upload_type=resumable
            2. Subir binario a rupload.facebook.com
            3. Esperar procesamiento (polling)
            4. Publicar

        Args:
            ruta_video: Ruta al archivo de video local (MP4 sanitizado).
            caption: Texto del caption del Reel.

        Retorna:
            str | None: Media ID del Reel publicado.
        """
        logger.info("\n🎬 === PUBLICANDO REEL (subida directa a Meta) ===")

        # Paso 1: Crear contenedor resumible
        resultado = self.crear_contenedor_resumible("REELS", caption)
        if not resultado:
            return None
        contenedor_id, uri_rupload = resultado

        # Paso 2: Subir el binario del video
        if not self.subir_binario_a_rupload(uri_rupload, ruta_video):
            return None

        # Paso 3: Esperar a que Meta procese el video
        if not self.esperar_contenedor_listo(contenedor_id):
            return None

        # Paso 4: Publicar
        return self.publicar_contenedor(contenedor_id)

    def publicar_story_desde_archivo(
        self,
        ruta_video: Path,
        tiempo_publicacion: datetime,
        indice: int,
    ) -> Optional[str]:
        """
        Publica una Story de video desde archivo local, con espera temporal.

        La API de Meta Graph NO soporta programación nativa de Stories
        a hora futura (a diferencia de los Reels). Por ello, el sistema
        espera el tiempo necesario y publica en el momento correcto.

        NOTA: Para Stories, Meta también soporta upload_type=resumable.
        El flujo es idéntico al de Reels.

        Args:
            ruta_video: Ruta al video local.
            tiempo_publicacion: Datetime UTC objetivo de publicación.
            indice: Número de Story (1-4) para logging.

        Retorna:
            str | None: Media ID de la Story publicada.
        """
        logger.info(
            "\n📱 === STORY %d (programada para %s UTC) ===",
            indice,
            tiempo_publicacion.strftime("%Y-%m-%d %H:%M"),
        )

        # Calcular tiempo de espera
        ahora_utc = datetime.now(ZONA_HORARIA_UTC)
        segundos_espera = (tiempo_publicacion - ahora_utc).total_seconds()

        if segundos_espera > 60:
            logger.info(
                "   ⏰ Esperando %.0f minutos antes de publicar la Story %d...",
                segundos_espera / 60, indice
            )
            time.sleep(max(0, segundos_espera))

        # Paso 1: Crear contenedor resumible para Story
        resultado = self.crear_contenedor_resumible("STORIES")
        if not resultado:
            return None
        contenedor_id, uri_rupload = resultado

        # Paso 2: Subir binario
        if not self.subir_binario_a_rupload(uri_rupload, ruta_video):
            return None

        # Paso 3: Esperar procesamiento
        if not self.esperar_contenedor_listo(contenedor_id):
            return None

        # Paso 4: Publicar
        media_id = self.publicar_contenedor(contenedor_id)
        if media_id:
            logger.info("   ✅ Story %d publicada. ID: %s", indice, media_id)
        return media_id

    def verificar_publicacion_reciente(self, horas_umbral: float = 12.0) -> bool:
        """
        Verifica si se ha publicado algún contenido en las últimas 'horas_umbral' en Instagram.
        Evita duplicaciones por ejecuciones crón solapadas.
        """
        logger.info("🔍 Comprobando si existen publicaciones recientes en Instagram...")
        resp = self._graph_get(f"/{self.account_id}/media", {"fields": "timestamp,media_type", "limit": "5"})
        if not resp or "data" not in resp:
            logger.info("   → No se pudieron recuperar las publicaciones o no existen.")
            return False

        ahora = datetime.now(timezone.utc)
        for media in resp["data"]:
            ts_str = media.get("timestamp")
            if not ts_str:
                continue
            try:
                ts_str_clean = ts_str.replace("+0000", "+00:00")
                ts_pub = datetime.fromisoformat(ts_str_clean)
                
                diferencia_horas = (ahora - ts_pub).total_seconds() / 3600.0
                if 0 <= diferencia_horas < horas_umbral:
                    logger.warning(
                        "   ⚠️ PUBLICACIÓN DETECTADA: Post publicado hace %.1f horas (ID: %s, Tipo: %s).",
                        diferencia_horas, media.get("id"), media.get("media_type")
                    )
                    return True
            except Exception as e:
                logger.warning("   ⚠️ Error al parsear timestamp '%s': %s", ts_str, e)
                continue

        logger.info("   ✅ Sin publicaciones en las últimas %.1f horas.", horas_umbral)
        return False


# ---------------------------------------------------------------------------
# MÓDULO 4: GENERACIÓN DE CAPTIONS Y HASHTAGS
# ---------------------------------------------------------------------------

def generar_caption_reel(video: dict) -> str:
    """
    Genera el caption del Reel con hashtags optimizados para alcance orgánico.

    Selecciona aleatoriamente un subconjunto de hashtags para variar entre
    publicaciones y evitar que Instagram penalice el uso repetitivo.

    Args:
        video: Metadatos del video para personalizar el caption.

    Retorna:
        str: Caption formateado (máx. 2200 caracteres, límite de Instagram).
    """
    hashtags_pool = [
        "#memesespañoles", "#humorlatino", "#memesenespañol", "#shitpost",
        "#humor", "#memes", "#españa", "#latino", "#gracioso", "#viralvideo",
        "#comedia", "#lolespaña", "#reels", "#instagramreels", "#memediario",
        "#humorismo", "#risas", "#memeespañol", "#shitpostenespañol",
        "#memeshispanos", "#trending", "#fyp", "#viral",
    ]

    # Seleccionar 15 hashtags aleatorios (Instagram soporta hasta 30)
    hashtags = random.sample(hashtags_pool, min(15, len(hashtags_pool)))
    texto_hashtags = " ".join(hashtags)

    frases_intro = [
        "😂 El mejor humor del día 🔥",
        "💀 No aguanto más 😂",
        "🤣 Esto me destruyó",
        "😭 Por qué esto me representa tanto",
        "☠️ El contenido que necesitaba hoy",
    ]

    intro = random.choice(frases_intro)
    return f"{intro}\n\n{texto_hashtags}"


def comprobar_horario_madrid() -> bool:
    """
    Comprueba si la ejecución actual corresponde al horario de publicación correcto (18:00 Madrid).
    Omitirá el primer cron (16:00 UTC) en invierno, ya que debe publicarse a las 17:00 UTC (18:00 Madrid).
    """
    import pytz
    tz_madrid = pytz.timezone("Europe/Madrid")
    ahora_madrid = datetime.now(tz_madrid)
    
    # Determinar si DST (Daylight Saving Time) está activo en Madrid
    es_verano = ahora_madrid.dst().total_seconds() > 0
    
    # En invierno (CET), el run de las 16:00 UTC (17:00 Madrid) debe omitirse
    if not es_verano and ahora_madrid.hour < 18:
        logger.info("   ⚠️ Omitiendo ejecución: en invierno (CET) se publica a las 17:00 UTC (18:00 Madrid). Hora actual: %s", ahora_madrid.strftime("%H:%M"))
        return False
        
    return True


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: ORQUESTADOR DEL FLUJO COMPLETO
# ---------------------------------------------------------------------------

def main():
    """
    Función principal que ejecuta el pipeline completo del sistema.

    Flujo:
        1. Cargar credenciales de Meta desde variables de entorno
        2. Descubrir TOP 5 videos en TikTok via yt-dlp
        3. Descargar y sanitizar con FFmpeg
        4. Subir directamente a Meta vía rupload.facebook.com
        5. Publicar Reel (#1) + 4 Stories distribuidas en 24h
    """
    logger.info("=" * 70)
    logger.info("🤖 SISTEMA DE PUBLICACIÓN AUTOMÁTICA DE MEMES v2.0")
    logger.info("   Stack: 100%% gratuito | yt-dlp + FFmpeg + Meta rupload")
    logger.info("   Hora: %s UTC", datetime.now(ZONA_HORARIA_UTC).isoformat())
    logger.info("=" * 70)

    # ── FASE 0: Credenciales ─────────────────────────────────────────────
    credenciales = cargar_credenciales()

    # ── FASE 0.5: Comprobaciones de duplicidad y horario ──────────────────
    solo_descubrir = os.environ.get("SOLO_DESCUBRIR", "false").lower() == "true"
    ejecucion_manual = os.environ.get("EJECUCION_MANUAL", "false").lower() == "true"

    if not solo_descubrir and not ejecucion_manual:
        # 1. Comprobar si el cron corresponde al horario de Madrid (18:00)
        if not comprobar_horario_madrid():
            logger.info("🛑 Cancelando ejecución para respetar el horario de Madrid. ¡Hasta luego!")
            sys.exit(0)

        # 2. Comprobar si ya se ha publicado algo en las últimas 12 horas en Instagram
        cliente_meta = SubidaResumibleMeta(
            access_token=credenciales["META_ACCESS_TOKEN"],
            instagram_account_id=credenciales["INSTAGRAM_ACCOUNT_ID"],
        )
        if cliente_meta.verificar_publicacion_reciente(horas_umbral=12.0):
            logger.info("🛑 Cancelando ejecución para evitar duplicaciones. ¡Hasta mañana!")
            sys.exit(0)

    # ── FASE 1: Descubrimiento en TikTok ─────────────────────────────────
    logger.info("\n📡 FASE 1: Descubrimiento de videos en TikTok (via yt-dlp)")
    logger.info("-" * 50)

    # cookies_file opcional: exportar desde navegador con sesión de TikTok
    # para evitar bloqueos de IP en el runner de GitHub Actions
    cookies_file = os.environ.get("TIKTOK_COOKIES_FILE", None)
    cliente_tiktok = ClienteTikTok(cookies_file=cookies_file)

    top_videos = descubrir_mejores_videos(cliente_tiktok)

    if not top_videos:
        logger.error("❌ Sin videos válidos. Abortando.")
        sys.exit(1)

    # ── FASE 2: Descarga y sanitización ──────────────────────────────────
    logger.info("\n🎬 FASE 2: Descarga y sanitización con FFmpeg")
    logger.info("-" * 50)

    with tempfile.TemporaryDirectory(prefix="memes_es_v2_") as dir_temp:
        directorio_trabajo = Path(dir_temp)
        logger.info("📁 Directorio temporal: %s", directorio_trabajo)

        videos_procesados = descargar_y_procesar_videos(
            top_videos, cliente_tiktok, directorio_trabajo
        )

        if not videos_procesados:
            logger.error("❌ No se procesaron videos. Abortando.")
            sys.exit(1)

        # ── FASE 3: Publicación en Instagram ─────────────────────────────
        logger.info("\n📸 FASE 3: Publicación en Instagram (subida directa a Meta)")
        logger.info("-" * 50)

        cliente_meta = SubidaResumibleMeta(
            access_token=credenciales["META_ACCESS_TOKEN"],
            instagram_account_id=credenciales["INSTAGRAM_ACCOUNT_ID"],
        )

        publicaciones_exitosas = []
        ahora_utc = datetime.now(ZONA_HORARIA_UTC)

        # ── Reel inmediato: Video #1 (mejor puntuación viral) ────────────
        video_reel = videos_procesados[0]
        ruta_reel  = video_reel["_ruta_local"]
        caption    = ""  # Reel subido sin descripción/caption

        media_id_reel = cliente_meta.publicar_reel_desde_archivo(ruta_reel, caption)

        if media_id_reel:
            publicaciones_exitosas.append({
                "tipo": "REEL",
                "media_id": media_id_reel,
                "viral_score": video_reel["_puntuacion_viral"],
            })
            logger.info("🎉 ¡REEL publicado! Media ID: %s", media_id_reel)
        else:
            logger.error("❌ No se pudo publicar el Reel.")

        # ── Stories: Videos #2 al #5, una cada 6 horas ───────────────────
        for i, video_story in enumerate(videos_procesados[1:], 1):
            tiempo_objetivo = ahora_utc + timedelta(seconds=INTERVALO_STORIES_SEGUNDOS * i)
            ruta_story      = video_story["_ruta_local"]

            media_id_story = cliente_meta.publicar_story_desde_archivo(
                ruta_story, tiempo_objetivo, i
            )

            if media_id_story:
                publicaciones_exitosas.append({
                    "tipo": f"STORY_{i}",
                    "media_id": media_id_story,
                    "hora_utc": tiempo_objetivo.strftime("%H:%M UTC"),
                })

        # ── Resumen final ─────────────────────────────────────────────────
        logger.info("\n" + "=" * 70)
        logger.info("📊 RESUMEN FINAL")
        logger.info("=" * 70)
        logger.info("✅ %d/%d publicaciones exitosas", len(publicaciones_exitosas), len(videos_procesados))

        for pub in publicaciones_exitosas:
            if pub["tipo"] == "REEL":
                logger.info(
                    "   🎬 REEL    │ Media ID: %s │ VS: %.1f",
                    pub["media_id"], pub.get("viral_score", 0)
                )
            else:
                logger.info(
                    "   📱 %-7s │ Media ID: %s │ Publicada a las %s",
                    pub["tipo"], pub["media_id"], pub.get("hora_utc", "?")
                )

        logger.info("\n🏁 Ejecución completada. ¡Hasta mañana! 🤖")
        logger.info("=" * 70)


if __name__ == "__main__":
    main()
