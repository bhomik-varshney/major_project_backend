from concurrent.futures import ThreadPoolExecutor
import time

import httpx
import pytest

from app.core.config import Settings
from app.providers.groq import GroqKeyManager, GroqProvider, ProviderUnavailable

KEYS = ['synthetic-slot-one', 'synthetic-slot-two', 'synthetic-slot-three']


def provider(handler, keys=KEYS):
    manager = GroqKeyManager(keys)
    return GroqProvider(manager, Settings(), client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))


def ok():
    return httpx.Response(200, json={'choices':[{'message':{'content':'{}'}}]})


def test_round_robin_and_exclusive_leases():
    manager = GroqKeyManager(KEYS)
    for cycle in range(3):
        with ThreadPoolExecutor(max_workers=4) as executor:
            leases = list(executor.map(manager.acquire, range(1, 5)))
        live = [lease for lease in leases if lease]
        assert len(live) == 3
        assert len({lease.index for lease in live}) == 3
        for lease in live:
            manager.finish(lease)
    for index in [0, 1, 2, 0]:
        lease = manager.acquire(9)
        assert lease.index == index
        manager.finish(lease)


@pytest.mark.parametrize('failure', [429, 500, 503, 401, 403, 'timeout', 'network'])
def test_rotates_on_retryable_failures_and_invalid_credentials(failure):
    seen = []
    def handler(request):
        seen.append(request.headers['authorization'])
        if len(seen) == 1:
            if failure == 'timeout':
                raise httpx.ReadTimeout('synthetic private detail')
            if failure == 'network':
                raise httpx.ConnectError('synthetic private detail')
            return httpx.Response(failure, headers={'retry-after':'8'}, text='private provider body')
        return ok()
    transport = provider(handler)
    assert transport.complete([], {}) == '{}'
    assert seen == ['Bearer '+KEYS[0], 'Bearer '+KEYS[1]]
    if failure in (401, 403):
        assert transport.manager.invalid[0]
    else:
        assert transport.manager.until[0] > time.monotonic()
    assert not any(transport.manager.owners)


def test_all_failures_are_bounded_safe_and_cool_down():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(429, text='private provider detail '+KEYS[0])
    transport = provider(handler)
    with pytest.raises(ProviderUnavailable) as error:
        transport.complete([], {})
    assert error.value.code == 'groq_rate_limit'
    assert len(calls) == 3
    assert 'private provider' not in str(error.value)
    assert all(key not in str(error.value) for key in KEYS)
    with pytest.raises(ProviderUnavailable) as error:
        transport.complete([], {})
    assert error.value.code == 'groq_keys_unavailable' and len(calls) == 3


def test_disabled_keys_deadline_and_missing_configuration():
    transport = provider(lambda request: httpx.Response(401))
    with pytest.raises(ProviderUnavailable) as error:
        transport.complete([], {})
    assert error.value.code == 'groq_invalid_credentials'
    assert all(transport.manager.invalid)
    with pytest.raises(ProviderUnavailable) as error:
        transport.complete([], {}, deadline=time.monotonic()-1)
    assert error.value.code == 'groq_timeout'
    with pytest.raises(ProviderUnavailable) as error:
        provider(lambda request: ok(), keys=[]).complete([], {})
    assert error.value.code == 'groq_not_configured'


def test_cooldown_expiry_and_killed_owner_release():
    manager = GroqKeyManager(KEYS[:1], cooldown=5)
    lease = manager.acquire(11, now=100)
    manager.finish(lease, 'temporary', retry_after=10, now=100)
    assert manager.acquire(12, now=109) is None
    lease = manager.acquire(12, now=110)
    assert lease is not None
    manager.release_owner(12)
    next_lease = manager.acquire(13, now=110)
    assert next_lease is not None
    manager.finish(lease, 'invalid', now=110)  # stale lease cannot modify new owner
    assert not manager.invalid[0] and manager.owners[0] == 13


def test_settings_keep_secrets_private_and_legacy_key_compatible():
    settings = Settings(groq_api_key='legacy-secret', groq_api_key_1=KEYS[0], groq_api_key_2=KEYS[1], groq_api_key_3=KEYS[1])
    assert [secret.get_secret_value() for secret in settings.groq_keys] == KEYS[:2]
    assert all(value not in repr(settings) for value in [*KEYS, 'legacy-secret'])
    assert Settings(groq_api_key='legacy-secret').groq_keys[0].get_secret_value() == 'legacy-secret'
    assert 'legacy-secret' not in repr(GroqKeyManager(['legacy-secret']).acquire(1))


def test_invalid_output_and_nonretryable_requests_do_not_spin():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(400, text='private error')
    with pytest.raises(ProviderUnavailable) as error:
        provider(handler).complete([], {})
    assert error.value.code == 'groq_request_rejected' and len(calls) == 1
    with pytest.raises(ProviderUnavailable) as error:
        provider(lambda request: httpx.Response(200, json={'choices':[]})).complete([], {})
    assert error.value.code == 'groq_invalid_output'
