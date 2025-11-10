# app/services/webhook.py
import os
import httpx
from typing import Optional
from app.config import logger


async def send_webhook_callback(
    file_id: str,
    embedded: bool,
    namespace: str,
    error: Optional[str] = None
):
    """
    Send webhook callback to LibreChat after embedding processing.

    Args:
        file_id: The file identifier
        embedded: Whether embedding was successful
        namespace: The namespace (sanitized user email)
        error: Optional error message if embedding failed
    """
    webhook_url = os.getenv("LIBRECHAT_WEBHOOK_URL")

    if not webhook_url:
        logger.debug("[WEBHOOK] No LIBRECHAT_WEBHOOK_URL configured, skipping callback")
        return

    # Construct full URL if not already complete
    if not webhook_url.endswith("/embedding"):
        # Remove trailing slash if present
        webhook_url = webhook_url.rstrip("/")
        webhook_url = f"{webhook_url}/api/files/webhooks/embedding"

    payload = {
        "file_id": file_id,
        "embedded": embedded,
        "namespace": namespace,
    }

    if error:
        payload["error"] = error

    try:
        logger.info(f"[WEBHOOK] Sending callback for file {file_id} to {webhook_url}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            response.raise_for_status()

            logger.info(f"[WEBHOOK] Successfully sent callback for file {file_id}")
            return response.json()

    except httpx.TimeoutException:
        logger.error(f"[WEBHOOK] Timeout sending callback for file {file_id}")
    except httpx.HTTPStatusError as e:
        logger.error(
            f"[WEBHOOK] HTTP error {e.response.status_code} for file {file_id}: {e.response.text}"
        )
    except Exception as e:
        logger.error(f"[WEBHOOK] Error sending callback for file {file_id}: {str(e)}")
