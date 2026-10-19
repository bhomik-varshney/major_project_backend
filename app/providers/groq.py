"""Bounded Groq transport with shared, process/thread-safe rotating credentials."""
from dataclasses import dataclass, field
import multiprocessing
import secrets
import time

import httpx
from pydantic import SecretStr


class ProviderUnavailable(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True)
class KeyLease:
    index: int
    owner: int
    credential: SecretStr = field(repr=False)


class GroqKeyManager:
    """Round-robin leases; state is shared with isolated CrewAI child processes."""
    def __init__(self, keys, cooldown=5, *, context=None):
        self._keys = tuple(key if isinstance(key, SecretStr) else SecretStr(key) for key in keys)
        self.cooldown = cooldown
        context = context or multiprocessing.get_context("spawn")
        n = len(self._keys)
        self.lock = context.RLock()
        self.cursor = context.Value("i", 0, lock=False)
        self.until = context.Array("d", n, lock=False)
        self.invalid = context.Array("b", n, lock=False)
        self.owners = context.Array("q", n, lock=False)
        self.failures = context.Array("i", n, lock=False)

    @property
    def configured(self):
        return bool(self._keys)

    def acquire(self, owner, *, now=None):
        stamp = time.monotonic() if now is None else now
        with self.lock:
            n = len(self._keys)
            for offset in range(n):
                index = (self.cursor.value + offset) % n
                if not self.invalid[index] and not self.owners[index] and self.until[index] <= stamp:
                    self.owners[index] = owner
                    self.cursor.value = (index + 1) % n
                    return KeyLease(index, owner, self._keys[index])
        return None

    def finish(self, lease, outcome="success", retry_after=0, *, now=None):
        stamp = time.monotonic() if now is None else now
        with self.lock:
            i = lease.index
            if self.owners[i] != lease.owner:
                return
            self.owners[i] = 0
            if outcome == "invalid":
                self.invalid[i] = True
            elif outcome == "temporary":
                self.failures[i] += 1
                delay = max(retry_after, self.cooldown * 2 ** min(self.failures[i] - 1, 4))
                self.until[i] = stamp + min(120, max(0, delay))
            elif outcome == "success":
                self.failures[i] = 0
                self.until[i] = 0

    def release_owner(self, owner):
        # Called after success, cancellation or forced process termination.
        with self.lock:
            for i in range(len(self._keys)):
                if self.owners[i] == owner:
                    self.owners[i] = 0


class GroqProvider:
    def __init__(self, manager, settings, *, client_factory=httpx.Client):
        self.manager, self.settings, self.client_factory = manager, settings, client_factory

    def complete(self, messages, schema, *, owner=None, deadline=None, schema_name="resume_extraction"):
        if not self.manager.configured:
            raise ProviderUnavailable("groq_not_configured", "Groq is not configured. Limited local extraction is available.")
        owner = owner or secrets.randbits(62) or 1
        deadline = deadline or time.monotonic() + self.settings.crew_timeout_seconds
        last_code = "groq_keys_unavailable"
        # A maximum of three requests per task; no waiting loops around exhausted keys.
        for _ in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderUnavailable("groq_timeout", "The provider time budget expired. Limited local extraction is available.")
            lease = self.manager.acquire(owner)
            if lease is None:
                break
            outcome, delay = "success", 0
            try:
                with self.client_factory(timeout=min(remaining, self.settings.groq_timeout_seconds)) as client:
                    response = client.post("https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": "Bearer " + lease.credential.get_secret_value()},
                        json={"model": self.settings.groq_model, "temperature": 0, "max_completion_tokens": self.settings.groq_max_output_tokens,
                            "messages": messages, "response_format": {"type": "json_schema", "json_schema": {
                                "name": schema_name, "strict": True, "schema": schema}}})
                if response.status_code in {401, 403}:
                    outcome, last_code = "invalid", "groq_invalid_credentials"
                    continue
                if response.status_code == 429 or response.status_code in {408, 409, 425} or response.status_code >= 500:
                    outcome = "temporary"
                    last_code = "groq_rate_limit" if response.status_code == 429 else "groq_unavailable"
                    try:
                        delay = min(120, max(0, float(response.headers.get("retry-after", "0"))))
                    except (ValueError, OverflowError):
                        delay = 0
                    continue
                if response.status_code == 413:
                    raise ProviderUnavailable("groq_input_limit", "Groq's input/token limit was exceeded. Limited local extraction is available.")
                if not response.is_success:
                    raise ProviderUnavailable("groq_request_rejected", "Groq rejected the structured request. Limited local extraction is available.")
                try:
                    content = response.json()["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or len(content) > 150000:
                        raise ValueError()
                    return content
                except (ValueError, KeyError, IndexError, TypeError):
                    raise ProviderUnavailable("groq_invalid_output", "Groq returned invalid output. Limited local extraction is available.") from None
            except httpx.TimeoutException:
                outcome, last_code = "temporary", "groq_timeout"
            except httpx.HTTPError:
                outcome, last_code = "temporary", "groq_unavailable"
            finally:
                self.manager.finish(lease, outcome, delay)
        raise ProviderUnavailable(last_code, "All configured Groq keys are invalid, busy, cooling down, or failed. Limited local extraction is available; retry later.")
