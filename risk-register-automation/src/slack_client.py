"""Slack API client for delivering risk notifications.

Supports:
- Sending DMs to users (via chat.postMessage to user ID)
- Posting to channels (via chat.postMessage to channel ID)
- Looking up users by email (users.lookupByEmail)
- Incoming webhook posting (fallback for channels)
- Rate limiting compliance (Tier 2: ~20 msgs/min)
- Retry logic with exponential backoff
- Dry-run mode for testing without a token

Usage:
    # Dry run (no token needed)
    client = SlackClient(dry_run=True)
    client.send_dm("alyssa.cheng@salesforce.com", message_blocks)

    # Real mode
    client = SlackClient(bot_token="xoxb-...", dry_run=False)
    client.send_dm("alyssa.cheng@salesforce.com", message_blocks)
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

# Slack API rate limits (Tier 2: ~20 requests per minute)
RATE_LIMIT_DELAY = 3.0  # seconds between messages to stay safe
MAX_RETRIES = 3
SLACK_API_BASE = "https://slack.com/api"


class SlackClientError(Exception):
    """Raised when a Slack API call fails."""
    pass


class SlackRateLimitError(SlackClientError):
    """Raised when we hit Slack's rate limit (429)."""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


class SlackClient:
    """Client for sending notifications via Slack API.

    Supports dry-run mode for testing without a real token.

    Usage:
        # Test mode - prints what would be sent
        client = SlackClient(dry_run=True)

        # Production mode
        client = SlackClient(bot_token="xoxb-...", target_user="alyssa.cheng@salesforce.com")
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        webhook_url: Optional[str] = None,
        dry_run: bool = True,
        target_user: Optional[str] = None,
        rate_limit_delay: float = RATE_LIMIT_DELAY,
    ):
        """Initialize the Slack client.

        Args:
            bot_token: Slack Bot User OAuth Token (xoxb-...).
            webhook_url: Incoming webhook URL for channel posting.
            dry_run: If True, print messages instead of sending.
            target_user: SAFETY: If set, only send to this email. All other
                        recipients are redirected here. Prevents accidental spam.
            rate_limit_delay: Seconds between messages (default 3s).
        """
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.dry_run = dry_run
        self.target_user = target_user  # HARDCODED SAFETY: only send to this person
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0.0
        self._user_id_cache: Dict[str, str] = {}
        self._messages_sent: List[Dict] = []  # Log of all sent/attempted messages

        if not dry_run and not bot_token and not webhook_url:
            raise SlackClientError(
                "Must provide bot_token or webhook_url when dry_run=False"
            )

    def send_dm(self, email: str, message: Dict) -> bool:
        """Send a direct message to a user by email.

        Args:
            email: Recipient's email address.
            message: Dict with 'text' and 'blocks' keys (from NotificationBuilder).

        Returns:
            True if sent successfully (or logged in dry-run mode).
        """
        # SAFETY: redirect to target_user if set
        original_email = email
        if self.target_user:
            email = self.target_user
            if original_email != self.target_user:
                logger.info(f"SAFETY: Redirecting DM from {original_email} → {email}")

        if self.dry_run:
            return self._dry_run_dm(email, message, original_email)

        # Look up Slack user ID from email
        user_id = self.lookup_user_by_email(email)
        if not user_id:
            logger.warning(f"Cannot DM {email}: user not found in Slack")
            return False

        # Send via chat.postMessage
        return self._post_message(channel=user_id, message=message)

    def send_to_channel(self, channel_id: str, message: Dict) -> bool:
        """Post a message to a Slack channel.

        Args:
            channel_id: Slack channel ID (e.g., C0123456789).
            message: Dict with 'text' and 'blocks' keys.

        Returns:
            True if sent successfully.
        """
        if self.dry_run:
            return self._dry_run_channel(channel_id, message)

        # Try webhook first if available
        if self.webhook_url:
            return self._post_webhook(message)

        # Fall back to chat.postMessage
        return self._post_message(channel=channel_id, message=message)

    def lookup_user_by_email(self, email: str) -> Optional[str]:
        """Look up a Slack user ID by email address.

        Uses users.lookupByEmail API. Caches results.

        Args:
            email: User's email address.

        Returns:
            Slack user ID (e.g., U0123456789) or None.
        """
        if email in self._user_id_cache:
            return self._user_id_cache[email]

        if self.dry_run:
            # Return a fake ID for dry-run
            fake_id = f"U_DRYRUN_{email.split('@')[0].upper()}"
            self._user_id_cache[email] = fake_id
            logger.info(f"[DRY RUN] Would look up Slack ID for {email} → {fake_id}")
            return fake_id

        self._respect_rate_limit()

        try:
            response = self._api_request(
                "users.lookupByEmail",
                params={"email": email},
            )
            if response.get("ok"):
                user_id = response["user"]["id"]
                self._user_id_cache[email] = user_id
                logger.info(f"Resolved {email} → {user_id}")
                return user_id
            else:
                error = response.get("error", "unknown")
                logger.warning(f"User lookup failed for {email}: {error}")
                return None
        except Exception as e:
            logger.error(f"User lookup error for {email}: {e}")
            return None

    def send_test_message(self, email: str) -> bool:
        """Send a simple test message to verify connectivity.

        Args:
            email: Recipient email to DM.

        Returns:
            True if successful.
        """
        test_msg = {
            "text": "🧪 Risk Register Automation - Test Message",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "🧪 *Risk Register Automation - Test*\n\n"
                            "If you're seeing this, Slack integration is working!\n"
                            f"Sent at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            "Mode: `dry_run`" if self.dry_run else "Mode: `live`"
                        ),
                    },
                },
            ],
        }
        return self.send_dm(email, test_msg)

    def get_delivery_log(self) -> List[Dict]:
        """Return log of all messages sent/attempted this session."""
        return self._messages_sent

    def get_stats(self) -> Dict:
        """Return delivery statistics."""
        total = len(self._messages_sent)
        success = sum(1 for m in self._messages_sent if m.get("success"))
        failed = total - success
        return {
            "total_attempted": total,
            "successful": success,
            "failed": failed,
            "dry_run": self.dry_run,
            "target_user": self.target_user,
        }

    # --- Private Methods ---

    def _post_message(self, channel: str, message: Dict) -> bool:
        """Post a message via chat.postMessage API."""
        self._respect_rate_limit()

        payload = {
            "channel": channel,
            "text": message.get("text", ""),
            "blocks": message.get("blocks", []),
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._api_request("chat.postMessage", json_data=payload)

                if response.get("ok"):
                    logger.info(f"Message sent to {channel}")
                    self._log_message(channel, message, success=True)
                    return True
                else:
                    error = response.get("error", "unknown")
                    logger.error(f"chat.postMessage failed: {error}")
                    if error == "ratelimited":
                        retry_after = int(response.get("headers", {}).get("Retry-After", 30))
                        logger.warning(f"Rate limited. Waiting {retry_after}s...")
                        time.sleep(retry_after)
                        continue
                    self._log_message(channel, message, success=False, error=error)
                    return False

            except Exception as e:
                logger.error(f"Request failed (attempt {attempt}): {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                self._log_message(channel, message, success=False, error=str(e))
                return False

        return False

    def _post_webhook(self, message: Dict) -> bool:
        """Post a message via incoming webhook."""
        if not requests:
            logger.error("requests library not installed")
            return False

        self._respect_rate_limit()

        try:
            response = requests.post(
                self.webhook_url,
                json=message,
                timeout=30,
            )
            if response.status_code == 200:
                logger.info("Webhook message sent successfully")
                self._log_message("webhook", message, success=True)
                return True
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning(f"Webhook rate limited. Retry after {retry_after}s")
                time.sleep(retry_after)
                return self._post_webhook(message)  # Retry once
            else:
                logger.error(f"Webhook failed: {response.status_code} {response.text}")
                self._log_message("webhook", message, success=False, error=response.text)
                return False
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            self._log_message("webhook", message, success=False, error=str(e))
            return False

    def _api_request(self, method: str, params: Dict = None, json_data: Dict = None) -> Dict:
        """Make a Slack API request."""
        if not requests:
            raise SlackClientError("requests library not installed. Run: pip install requests")

        url = f"{SLACK_API_BASE}/{method}"
        headers = {"Authorization": f"Bearer {self.bot_token}"}

        if json_data:
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=json_data, timeout=30)
        else:
            response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 30))
            raise SlackRateLimitError(retry_after)

        return response.json()

    def _respect_rate_limit(self):
        """Ensure we don't exceed Slack's rate limit."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _dry_run_dm(self, email: str, message: Dict, original_email: str) -> bool:
        """Log what would be sent in dry-run mode."""
        redirected = f" (redirected from {original_email})" if original_email != email else ""
        print(f"\n{'─' * 60}")
        print(f"📨 [DRY RUN] Slack DM to: {email}{redirected}")
        print(f"{'─' * 60}")
        print(f"Subject: {message.get('text', '')}")
        print(f"Blocks: {len(message.get('blocks', []))} blocks")
        print(f"\nPreview:")
        for block in message.get("blocks", []):
            if block.get("type") == "header":
                print(f"  ┌ {block['text']['text']}")
            elif block.get("type") == "section":
                text = block.get("text", {}).get("text", "")
                for line in text.split("\n"):
                    print(f"  │ {line}")
            elif block.get("type") == "divider":
                print(f"  ├{'─' * 50}")
            elif block.get("type") == "context":
                for elem in block.get("elements", []):
                    print(f"  │ _{elem.get('text', '')}_")
        print(f"  └{'─' * 50}")
        print()

        self._log_message(f"DM:{email}", message, success=True, dry_run=True)
        return True

    def _dry_run_channel(self, channel_id: str, message: Dict) -> bool:
        """Log channel post in dry-run mode."""
        print(f"\n{'─' * 60}")
        print(f"📢 [DRY RUN] Channel post to: {channel_id}")
        print(f"{'─' * 60}")
        print(f"Subject: {message.get('text', '')}")
        print(json.dumps(message, indent=2)[:500])
        print()

        self._log_message(channel_id, message, success=True, dry_run=True)
        return True

    def _log_message(self, destination: str, message: Dict, success: bool,
                     error: str = None, dry_run: bool = False):
        """Log a message attempt."""
        self._messages_sent.append({
            "timestamp": datetime.now().isoformat(),
            "destination": destination,
            "text": message.get("text", ""),
            "success": success,
            "error": error,
            "dry_run": dry_run,
        })
