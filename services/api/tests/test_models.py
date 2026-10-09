import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import PROVIDERS, Settings, create_app


@pytest.mark.parametrize('provider_id', ['openai', 'anthropic', 'deepseek', 'openrouter'])
def test_provider_selection_request_protocol_and_citations(tmp_path, monkeypatch, provider_id):
    settings = Settings(tmp_path, provider_keys={provider_id: 'test-key'})
    real_client = httpx.Client
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append((str(request.url), dict(request.headers), payload))
        prompt = payload['messages'][-1]['content']
        passage = json.loads(prompt.split('Evidence:\n')[1])[0]
        result = json.dumps({'answer': 'The source records a payment.', 'passage_ids': [passage['passage_id']]})
        if provider_id == 'anthropic':
            return httpx.Response(200, json={'content': [{'type': 'text', 'text': result}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': result}}]})

    monkeypatch.setattr('app.httpx.Client', lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    with TestClient(create_app(settings)) as client:
        case = client.post('/api/cases', json={'name': 'Provider test'}).json()['id']
        client.post('/api/cases/' + case + '/documents', files={'file': ('payment.txt', b'A payment of 500 was agreed.')})
        model = PROVIDERS[provider_id]['models'][0]
        config = client.put('/api/models/selection', json={'provider_id': provider_id, 'model': model}).json()
        assert config['provider']['id'] == provider_id
        assert config['provider']['configured']
        assert len(config['providers']) == 4
        assert 'test-key' not in json.dumps(config)
        body = {'question': 'payment', 'use_ai': True, 'provider_id': provider_id, 'model': model}
        assert client.post('/api/cases/' + case + '/chat', json=body).status_code == 409
        assert not sent
        result = client.post('/api/cases/' + case + '/chat', json={**body, 'approved_external': True})
        assert result.status_code == 200 and result.json()['citations']
        endpoint, headers, payload = sent[0]
        assert payload['model'] == model
        assert endpoint.startswith(PROVIDERS[provider_id]['base_url'])
        if provider_id == 'anthropic':
            assert endpoint.endswith('/messages')
            assert headers['x-api-key'] == 'test-key' and 'system' in payload
            assert payload['max_tokens'] == 4096
        else:
            assert endpoint.endswith('/chat/completions')
            assert headers['authorization'] == 'Bearer test-key'
    with TestClient(create_app(settings)) as client:
        config = client.get('/api/settings').json()
        assert config['provider']['id'] == provider_id and config['provider']['model'] == model


def test_changed_model_invalidates_prior_consent_and_keys_are_optional(tmp_path):
    settings = Settings(tmp_path, provider_keys={'openai': 'test-key', 'anthropic': 'test-key'})
    with TestClient(create_app(settings)) as client:
        case = client.post('/api/cases', json={'name': 'Consent test'}).json()['id']
        client.post('/api/cases/' + case + '/documents', files={'file': ('payment.txt', b'A payment of 500.')})
        assert client.put('/api/models/selection', json={'provider_id': 'anthropic', 'model': 'claude-sonnet-4-6'}).status_code == 200
        stale = client.post('/api/cases/' + case + '/chat', json={'question': 'payment', 'use_ai': True, 'approved_external': True, 'provider_id': 'openai', 'model': 'gpt-4.1-mini'})
        assert stale.status_code == 409 and 'changed' in stale.json()['detail']
        missing = client.put('/api/models/selection', json={'provider_id': 'deepseek', 'model': 'deepseek-chat'}).json()
        assert not missing['provider']['configured']
        assert client.post('/api/cases/' + case + '/chat', json={'question': 'payment', 'use_ai': True, 'approved_external': True}).status_code == 409
        assert client.put('/api/models/selection', json={'provider_id': 'unknown', 'model': 'model'}).status_code == 422
        assert client.put('/api/models/selection', json={'provider_id': 'openai', 'model': '   '}).status_code == 422


def test_running_agent_keeps_starting_provider_and_model(tmp_path, monkeypatch):
    import threading
    import time
    settings = Settings(tmp_path, provider_keys={'openai': 'test-key', 'anthropic': 'test-key'})
    real_client = httpx.Client
    started, release = threading.Event(), threading.Event()
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append((str(request.url), payload['model']))
        started.set()
        assert release.wait(5)
        evidence = json.loads(payload['messages'][-1]['content'].split('Evidence:\n')[1])
        response = json.dumps({'findings': [{'passage_id': evidence[0]['passage_id'], 'analysis': 'The source records a payment.'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': response}}]})

    monkeypatch.setattr('app.httpx.Client', lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    with TestClient(create_app(settings)) as client:
        case = client.post('/api/cases', json={'name': 'Snapshot test'}).json()['id']
        client.post('/api/cases/' + case + '/documents', files={'file': ('payment.txt', b'Payment of 500.')})
        agent = client.get('/api/cases/' + case + '/agents').json()[1]
        response = client.post('/api/cases/' + case + '/reviews', json={'agent_ids': [agent['id']], 'use_ai': True, 'approved_external': True})
        assert response.status_code == 202
        try:
            assert started.wait(5)
            client.put('/api/models/selection', json={'provider_id': 'anthropic', 'model': 'claude-sonnet-4-6'})
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = client.get('/api/cases/' + case + '/tasks').json()[0]
            if task['status'] in {'completed', 'failed'}:
                break
            time.sleep(.02)
        assert task['status'] == 'completed'
        assert task['provider'] == 'ChatGPT / OpenAI' and task['model'] == 'gpt-4.1-mini'
        assert sent == [('https://api.openai.com/v1/chat/completions', 'gpt-4.1-mini')]
