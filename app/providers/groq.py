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

    def retry_delay(self):
        with self.lock:
            waits=[max(0,self.until[i]-time.monotonic()) for i in range(len(self._keys)) if not self.invalid[i] and not self.owners[i]]
        return min(waits) if waits else None

    def release_owner(self, owner):
        # Called after success, cancellation or forced process termination.
        with self.lock:
            for i in range(len(self._keys)):
                if self.owners[i] == owner:
                    self.owners[i] = 0


class GroqProvider:
    def __init__(self, manager, settings, *, client_factory=httpx.Client):
        self.manager, self.settings, self.client_factory = manager, settings, client_factory

    def complete(self, messages, schema, *, owner=None, deadline=None, schema_name="resume_extraction", model=None, json_mode=False, max_output_tokens=None, allow_wait=False):
        if not self.manager.configured:
            raise ProviderUnavailable("groq_not_configured", "Groq is not configured. Limited local extraction is available.")
        owner = owner or secrets.randbits(62) or 1
        deadline = deadline or time.monotonic() + self.settings.crew_timeout_seconds
        last_code = "groq_keys_unavailable"
        # Three rotations; opt-in tasks can wait once and make one final request.
        waited=False
        for _ in range(4 if allow_wait else 3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderUnavailable("groq_timeout", "The provider time budget expired. Limited local extraction is available.")
            lease = self.manager.acquire(owner)
            if lease is None and allow_wait and not waited:
                pause=self.manager.retry_delay()
                if pause is not None and 0<pause<=min(60,remaining-1):
                    getattr(self,'on_wait',lambda:None)()
                    time.sleep(pause+.05)
                    waited=True
                    lease=self.manager.acquire(owner)
            if lease is None:
                break
            outcome, delay = "success", 0
            remaining=max(.01,deadline-time.monotonic())
            try:
                selected_model=model or self.settings.text_model
                payload={"model":selected_model,"temperature":0,"max_completion_tokens":max_output_tokens or self.settings.groq_max_output_tokens,
                    "messages":messages,"response_format":{"type":"json_object"} if json_mode else {"type":"json_schema","json_schema":{
                        "name":schema_name,"strict":True,"schema":schema}}}
                if selected_model.startswith('openai/gpt-oss-'):
                    payload.update(reasoning_effort='low',include_reasoning=False)
                elif selected_model.startswith('qwen/qwen3.8-'):
                    payload.update(reasoning_effort='none')
                with self.client_factory(timeout=min(remaining,self.settings.groq_timeout_seconds)) as client:
                    response=client.post('https://api.groq.com/openai/v1/chat/completions',
                        headers={'Authorization':'Bearer '+lease.credential.get_secret_value()},json=payload)
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
                if response.status_code == 404:
                    raise ProviderUnavailable('groq_model_unavailable', 'The configured model is unavailable. Other configured models or local evidence will be used.')
                if not response.is_success:
                    try:
                        code = response.json().get('error',{}).get('code','')
                    except (ValueError, AttributeError):
                        code = ''
                    if code in {'json_validate_failed','failed_generation'}:
                        raise ProviderUnavailable('groq_invalid_output','Groq could not return a response matching the required JSON schema. Completed extraction is retained when the quality review fails.')
                    if code in {'model_decommissioned','model_not_found','model_not_active'}:
                        raise ProviderUnavailable('groq_model_unavailable','The configured model is unavailable. Other configured models or local evidence will be used.')
                    raise ProviderUnavailable("groq_request_rejected", "Groq rejected the structured request. Limited local extraction is available.")
                try:
                    choice = response.json()["choices"][0]
                    if choice.get('finish_reason')=='length':
                        raise ProviderUnavailable('groq_output_truncated','The model reached its completion limit before finishing the response. Completed extraction is retained when the quality review fails.')
                    content = choice["message"]["content"]
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

    def available_models(self, *, owner=None, deadline=None):
        """Check live availability; no response bodies or credentials leave transport."""
        owner = owner or secrets.randbits(62) or 1
        deadline = deadline or time.monotonic()+self.settings.groq_timeout_seconds
        last = 'groq_keys_unavailable'
        for _ in range(3):
            remaining=deadline-time.monotonic()
            if remaining<=0:
                break
            lease=self.manager.acquire(owner)
            if not lease:
                break
            outcome='success'
            try:
                with self.client_factory(timeout=min(remaining,self.settings.groq_timeout_seconds)) as client:
                    response=client.get('https://api.groq.com/openai/v1/models',headers={'Authorization':'Bearer '+lease.credential.get_secret_value()})
                if response.status_code in {401,403}:
                    outcome,last='invalid','groq_invalid_credentials';continue
                if response.status_code==429 or response.status_code>=500:
                    outcome,last='temporary','groq_unavailable';continue
                if not response.is_success:
                    raise ProviderUnavailable('groq_models_unavailable','Model availability could not be checked. Local evidence remains available.')
                try:
                    return {row['id'] for row in response.json()['data'] if isinstance(row,dict) and isinstance(row.get('id'),str)}
                except (ValueError,KeyError,TypeError):
                    raise ProviderUnavailable('groq_models_unavailable','Model availability returned invalid data. Local evidence remains available.') from None
            except httpx.HTTPError:
                outcome,last='temporary','groq_unavailable'
            finally:
                self.manager.finish(lease,outcome)
        raise ProviderUnavailable(last,'Model availability could not be checked with the configured keys. Local evidence remains available.')
