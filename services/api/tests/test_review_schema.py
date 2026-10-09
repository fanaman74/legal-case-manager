import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Settings, create_app


@pytest.mark.parametrize('content,expected', [
    ({}, 'failed'), ({'findings': 'wrong type'}, 'failed'),
    ({'findings': [None]}, 'failed'), ({'findings': []}, 'completed'),
])
def test_review_response_schema(tmp_path, monkeypatch, content, expected):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]}))
    monkeypatch.setattr('app.httpx.Client', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = Settings(tmp_path, 'https://example.test/v1', 'test-model', 'test-key')
    with TestClient(create_app(settings)) as client:
        case = client.post('/api/cases', json={'name': 'Schema test'}).json()['id']
        client.post('/api/cases/' + case + '/documents', files={'file': ('evidence.txt', b'A payment of 500 was agreed.')})
        agent = client.get('/api/cases/' + case + '/agents').json()[1]
        client.post('/api/cases/' + case + '/reviews', json={'agent_ids': [agent['id']], 'use_ai': True, 'approved_external': True})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = client.get('/api/cases/' + case + '/tasks').json()[0]
            if task['status'] in {'completed', 'failed'}:
                break
            time.sleep(.02)
        assert task['status'] == expected and task['findings'] == [] and task['mode'] == 'ai'
        if expected == 'failed':
            assert task['error']
