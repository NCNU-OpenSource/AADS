"""
Escalation webhook for the Knowledge Agent (ADR-006).

When an execution pauses for review, the executor surfaces the escalation to
the central console. The dashboard reads execution_escalations from the DB
anyway, so this webhook is an optional push channel (Slack/Teams/ntfy relay)
gated by AADS_ESCALATION_WEBHOOK_URL. Failures are logged and swallowed —
notification must never break the pause itself.

Deliberately self-contained: layer4 runs in its own container and imports
nothing from layer2 (the notification_hub there is a different service).
"""
import json
import logging
import os
from typing import Any, Dict

import aiohttp

logger = logging.getLogger("knowledge-agent.notify")

ESCALATION_WEBHOOK_URL = os.getenv("AADS_ESCALATION_WEBHOOK_URL", "")
WEBHOOK_TIMEOUT_SECONDS = int(os.getenv("AADS_ESCALATION_WEBHOOK_TIMEOUT", "10"))


async def post_escalation_webhook(payload: Dict[str, Any]) -> bool:
    """POST the escalation payload to the configured webhook. Returns delivery success."""
    if not ESCALATION_WEBHOOK_URL:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ESCALATION_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS),
            ) as response:
                if response.status >= 300:
                    logger.warning(
                        "escalation webhook returned %s: %s",
                        response.status,
                        (await response.text())[:500],
                    )
                    return False
                return True
    except Exception as e:  # noqa: BLE001 - notification must never break the pause
        logger.warning("escalation webhook failed: %s (payload=%s)", e, json.dumps(payload)[:500])
        return False
