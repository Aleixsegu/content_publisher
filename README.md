# 🤖 Sistema Automatizado de Curación y Publicación de Memes v2.0
### TikTok → Instagram Reels & Stories (Stack 100% Gratuito)

Este proyecto implementa un pipeline serverless automatizado que se ejecuta diariamente en GitHub Actions para buscar, filtrar, sanitizar y publicar memes virales de TikTok en Instagram Reels y Stories.

A diferencia de la v1.0, esta versión elimina todas las dependencias de pago:
- **Scraping y Descarga:** Realizado de forma gratuita y sin API keys utilizando `yt-dlp`.
- **Hosting de Medios:** Sin Cloudinary ni AWS S3. Los videos se suben directamente como binarios a los servidores de Meta usando su API de carga resumible (`rupload.facebook.com`).

---

## 📁 Estructura del Repositorio

El repositorio está estructurado de la siguiente manera:

```text
content_publisher/
├── .github/
│   └── workflows/
│       └── main.yml         # Workflow de GitHub Actions (programación cron diaria)
├── publicar_meme.py         # Script de ejecución principal en Python
├── requirements.txt         # Dependencias del proyecto (yt-dlp, requests, pytz)
└── README.md                # Esta documentación
```

---

## 🛠️ Requisitos de Entorno e Instalación

Para desarrollo o diagnóstico local, necesitas:

1. **Python 3.11 o superior**
2. **FFmpeg** instalado en tu sistema y disponible en el PATH.
3. Instalar las dependencias listadas en `requirements.txt`:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🔑 Configuración de Secretos en GitHub

Para que el workflow funcione automáticamente en tu repositorio de GitHub, debes configurar los siguientes secretos en **Settings → Secrets and variables → Actions → New repository secret**:

### 1. Secretos Obligatorios (Meta API)

*   `META_ACCESS_TOKEN`: Token de acceso de usuario de Meta Graph API de larga duración (válido por 60 días). Debe incluir los scopes actualizados (ver sección de renovación).
*   `INSTAGRAM_ACCOUNT_ID`: El ID numérico de tu cuenta de Instagram Business o Creator.
*   `META_APP_ID`: El App ID de tu aplicación registrada en Meta for Developers (requerido para el endpoint `rupload`).

### 2. Secreto Opcional (TikTok Cookies)

*   `TIKTOK_COOKIES`: Contenido del archivo de cookies de TikTok en formato Netscape.
    *   *¿Por qué es necesario?* GitHub Actions ejecuta los runners desde datacenters públicos cuyas direcciones IP pueden ser bloqueadas por TikTok (error HTTP 403 o cero videos devueltos).
    *   *Cómo obtenerlo:* Instala la extensión de Chrome **"Get cookies.txt LOCALLY"**, entra a tiktok.com con tu sesión iniciada, exporta las cookies en formato Netscape, copia el contenido y guárdalo como este secreto. El workflow lo escribirá en `/tmp/tiktok_cookies.txt` de manera transparente.

---

## 🔄 Renovación del Token de Meta (Cada 60 días)

Dado que Meta no proporciona tokens de acceso de usuario permanentes por motivos de seguridad, debes renovar tu `META_ACCESS_TOKEN` cada 60 días siguiendo estos pasos:

1. Ve a [Meta for Developers](https://developers.facebook.com/) → **Graph API Explorer**.
2. Selecciona tu **App ID** en la esquina superior derecha.
3. En la sección **User or Page**, selecciona tu usuario de Facebook.
4. En **Permissions**, asegúrate de tener aprobados y seleccionados los siguientes scopes actualizados (Enero 2025):
   *   `instagram_business_basic` (Acceso a información básica de perfil)
   *   `instagram_business_content_publish` (Publicación de Reels y Stories)
   *   `pages_show_list` (Listar páginas de Facebook vinculadas)
   *   `pages_read_engagement` (Métricas de página)
5. Haz clic en **Generate Access Token** y copia el token de corta duración.
6. Intercambia el token corto por el de larga duración (60 días) realizando una petición GET HTTP a este enlace (puedes pegarlo en tu navegador sustituyendo las variables entre llaves):
   ```text
   https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id={META_APP_ID}&client_secret={META_APP_SECRET}&fb_exchange_token={TOKEN_CORTO}
   ```
7. Copia el valor de `access_token` retornado y actualiza el Secret `META_ACCESS_TOKEN` en GitHub.

---

## ⚙️ Configuración y Límites de Tiempo

### Intervalo de Stories (GitHub Actions)
En el script `publicar_meme.py`, la constante `INTERVALO_STORIES_SEGUNDOS` está configurada a **75 minutos** (`4500` segundos).
*   *Nota:* Originalmente las Stories se programan a intervalos de 6 horas para distribuirse en el día. Sin embargo, debido a que GitHub Actions limita la duración de cualquier runner a un máximo absoluto de **6 horas** (y el workflow tiene un límite preventivo de **5.5 horas** / `330` minutos), el script ha sido adaptado para publicar las stories secuencialmente cada 75 minutos para aprovechar al máximo el tiempo de vida de la sesión (5 horas de ejecución total para las 4 stories) sin llegar al timeout. 

Si deseas hospedar este bot en un servidor persistente (VPS, Raspberry Pi, etc.), puedes reajustar `INTERVALO_STORIES_SEGUNDOS` a `6 * 3600` (6 horas) para publicar de forma distribuida a lo largo del día.

---

## ⚠️ Aviso Legal y Términos de Servicio

El uso de `yt-dlp` para scraping de TikTok se realiza en base a las APIs públicas internas de la plataforma. TikTok prohíbe el scraping automatizado en sus Términos de Servicio. Este sistema ha sido diseñado exclusivamente con fines educativos y de demostración. Los autores no se responsabilizan del uso indebido de este código ni de posibles penalizaciones de derechos de autor por republicar videos de terceros sin autorización explícita.