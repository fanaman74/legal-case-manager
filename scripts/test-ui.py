"""Browser smoke test against a disposable running instance, never a real case store.

CASEFILES_UI_TEST_URL=http://127.0.0.1:8001 python scripts/test-ui.py
Set CASEFILES_CHROMIUM to an existing system browser or install Playwright Chromium.
"""
import json
import os
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

url = os.environ.get('CASEFILES_UI_TEST_URL', '')
if not url:
    raise SystemExit('Set CASEFILES_UI_TEST_URL to a disposable test instance.')
output = Path(os.environ.get('CASEFILES_UI_TEST_OUTPUT', '/tmp/legal-case-manager-ui-results'))
output.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CASEFILES_CHROMIUM', '/usr/bin/chromium'), headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator('.loading-workspace')).not_to_be_visible()
    if not page.get_by_role('textbox', name='Case name').is_visible():
        page.get_by_role('button', name='New case', exact=True).click()
    page.get_by_role('textbox', name='Case name').fill('Interface check')
    page.get_by_role('button', name='Create case', exact=True).click()
    expect(page.get_by_role('heading', name='Your case, seen from every angle.')).to_be_visible()
    expect(page.get_by_label('Your workspace')).to_contain_text('Interface check')
    case_id = page.get_by_label('Your workspace').input_value()
    page.screenshot(path=str(output / 'desktop-workspace.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(output / 'mobile-workspace.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile welcome overflows'
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_label('Choose case documents').set_input_files({
        'name': 'browser-test-evidence.txt', 'mimeType': 'text/plain',
        'buffer': b'TEST FIXTURE ONLY.\nOn 12 March 2025 a payment of GBP 500 was agreed by email.\nThe contract required payment within 14 days.',
    })
    expect(page.get_by_text('1 document uploaded and ready to review.', exact=True)).to_be_visible()
    page.get_by_role('button', name='Documents', exact=True).click()
    expect(page.get_by_text('browser-test-evidence.txt', exact=True)).to_be_visible()
    page.get_by_role('button', name='Topic agents', exact=True).click()
    page.get_by_role('button', name='Run all agents', exact=True).click()
    expect(page.get_by_role('heading', name='Review tasks', exact=True)).to_be_visible()
    expect(page.locator('.status-badge.completed')).to_have_count(4, timeout=20000)
    page.locator('.task-summary').first.click()
    expect(page.locator('.finding')).to_have_count(1)
    page.locator('.citation').first.click()
    expect(page.get_by_role('dialog', name='browser-test-evidence.txt', exact=True)).to_be_visible()
    expect(page.locator('.highlighted-passage')).to_contain_text('GBP 500')
    page.locator('.evidence-dialog').evaluate('(dialog) => Promise.all(dialog.getAnimations().map(animation => animation.finished))')
    page.screenshot(path=str(output / 'source-dialog.png'), full_page=True)
    page.get_by_role('button', name='Close source document').click()
    page.screenshot(path=str(output / 'desktop-findings.png'), full_page=True)
    page.get_by_role('button', name='Case assistant', exact=True).click()
    page.get_by_role('textbox', name='Ask a question about your case').fill('Find the payment')
    page.get_by_role('button', name='Send question').click()
    expect(page.locator('.message.assistant')).to_have_count(1)
    expect(page.locator('.chat-citations')).to_contain_text('GBP 500')
    page.get_by_role('button', name='Topic agents', exact=True).click()
    page.get_by_role('button', name='Create agent', exact=True).click()
    page.get_by_label('Name', exact=True).fill('Payment specialist')
    page.get_by_label('Search terms', exact=True).fill('payment,500')
    page.get_by_label('Review instruction', exact=True).fill('Find payment evidence and cite its source.')
    page.get_by_role('button', name='Save agent', exact=True).click()
    expect(page.get_by_role('heading', name='Payment specialist', exact=True)).to_be_visible()
    page.get_by_role('button', name='Edit agent Payment specialist').click()
    page.get_by_label('Search terms', exact=True).fill('payment,contract')
    page.get_by_role('button', name='Save agent', exact=True).click()
    expect(page.get_by_role('heading', name='Edit topic agent')).not_to_be_visible()
    with page.expect_download() as download:
        page.get_by_role('button', name='Export review', exact=True).click()
    exported = json.loads(Path(download.value.path()).read_text())
    assert len(exported['reviews']) == 4 and len(exported['agents']) == 5
    page.set_viewport_size({'width': 390, 'height': 844})
    for view in ['Documents', 'Topic agents', 'Review tasks', 'Models']:
        page.get_by_role('button', name=view, exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), view + ' overflows'
    for name, model in [('Claude / Anthropic', 'claude-sonnet-4-6'), ('ChatGPT / OpenAI', 'gpt-4.1-mini'), ('DeepSeek', 'deepseek-chat'), ('OpenRouter', 'openai/gpt-4.1-mini')]:
        page.get_by_role('radio', name=name, exact=False).check()
        page.get_by_label('Model identifier').fill(model)
        page.get_by_role('button', name='Use this model', exact=True).click()
        expect(page.locator('.active-model')).to_contain_text(model)
    page.screenshot(path=str(output / 'mobile-models.png'), full_page=True)
    page.get_by_role('button', name='Open settings').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Settings overflow'
    page.screenshot(path=str(output / 'mobile-settings.png'), full_page=True)

    # Provider metadata and requests are intercepted locally. No external sending.
    context = browser.new_context(viewport={'width': 1200, 'height': 900})
    consent_page = context.new_page()
    with urllib.request.urlopen(url + '/api/settings') as response:
        settings = json.load(response)
    settings['provider'] = {'id': 'openrouter', 'name': 'OpenRouter', 'configured': True, 'external': True, 'host': 'example.test', 'model': 'test-model', 'max_excerpts': 18}
    consent_page.route('**/api/settings', lambda route: route.fulfill(json=settings))
    requests = []

    def intercept_chat(route):
        requests.append(route.request.post_data_json)
        route.fulfill(json={'id': 'test-only', 'role': 'assistant', 'text': 'Test response', 'citations': [], 'mode': 'ai'})

    consent_page.route('**/api/cases/*/chat', intercept_chat)
    consent_page.goto(url)
    consent_page.get_by_label('Your workspace').select_option(case_id)
    expect(consent_page.locator('.loading-workspace')).not_to_be_visible()
    consent_page.get_by_role('checkbox', name='AI analysis').check()
    consent_page.get_by_role('textbox', name='Ask a question about your case').fill('Find the payment')
    consent_page.get_by_role('button', name='Send question').click()
    expect(consent_page.get_by_role('dialog')).to_be_visible()
    expect(consent_page.get_by_role('dialog', name='Approve external AI analysis', exact=True)).to_be_visible()
    expect(consent_page.get_by_role('dialog')).to_contain_text('example.test')
    expect(consent_page.get_by_role('dialog')).to_contain_text('browser-test-evidence.txt')
    consent_page.screenshot(path=str(output / 'approval-dialog.png'), full_page=True)
    assert not requests
    consent_page.get_by_role('button', name='Keep it local').click()
    assert not requests
    consent_page.get_by_role('button', name='Send question').click()
    consent_page.get_by_role('button', name='Approve and continue').click()
    expect(consent_page.get_by_role('dialog')).not_to_be_visible()
    expect(consent_page.get_by_role('textbox', name='Ask a question about your case')).to_have_value('')
    assert len(requests) == 1 and requests[0]['approved_external'] is True
    assert requests[0]['document_ids'] == [exported['documents'][0]['id']]
    # A valid empty AI response must not claim that retrieval found no passages.
    consent_page.route('**/api/cases/*/tasks', lambda route: route.fulfill(json=[{
        'id': 'empty-ai-test', 'agent_id': exported['agents'][0]['id'], 'name': 'Empty analysis — test fixture',
        'status': 'completed', 'mode': 'ai', 'created_at': '2026-10-09T12:00:00Z', 'findings': [],
    }]))
    consent_page.reload()
    consent_page.get_by_label('Your workspace').select_option(case_id)
    consent_page.get_by_role('button', name='Review tasks', exact=True).click()
    consent_page.locator('.task-summary').click()
    expect(consent_page.locator('.no-findings')).to_contain_text('This analysis returned no cited findings.')
    expect(consent_page.locator('.no-findings')).not_to_contain_text('No matching passages were found')
    consent_page.screenshot(path=str(output / 'empty-ai-review.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('Browser workflow passed: upload, four agents, citations, chat, custom agent, export, mobile layouts and external approval.')
print('Screenshots:', output)
