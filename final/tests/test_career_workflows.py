"""Contract and integration tests; no external provider calls or real documents."""
import io
import os
import socket
import tempfile
import unittest
import httpx
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
import web_app
from career import documents, providers
from career.workflows import anchor_quote, conservative_findings
from career.demo import example
from career.schemas import (Fact, ProfileExtraction, Finding, MatchResult, ChangeResult,
                            ResumeChange, ResearchResult, Claim, LetterResult, ResearchPlan, ResearchDraft, ResearchPoint)


class CareerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'SBER21_DB': str(Path(self.tmp.name) / 'career.db')})
        self.env.start()
        self.client = TestClient(web_app.app, base_url='http://localhost')
        self.app = self.client.post('/api/applications', json={'demo': True}).json()
        self.url = '/api/applications/' + self.app['id']

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def mutate(self, path, data=None, method='post', expected=200):
        response = getattr(self.client, method)(self.url + '/' + path,
                    json={'version': self.app['version'], **(data or {})})
        self.assertEqual(response.status_code, expected, response.text[:500])
        if expected == 200:
            self.app = response.json()
        return response

    def test_offline_demo_never_calls_ai_and_survives_new_client(self):
        with patch.object(providers, 'structured', side_effect=AssertionError('network')):
            a = self.client.post('/api/applications', json={'demo': True}).json()
        self.assertTrue(a['demo'])
        self.assertEqual(a['mode'], 'example')
        with TestClient(web_app.app, base_url='http://localhost') as another:
            restored = another.get('/api/applications/' + a['id']).json()
        self.assertEqual(restored['facts'], a['facts'])

    def test_decisions_control_document_and_confirmation_controls_export(self):
        self.assertEqual(self.client.get(self.url + '/export/resume.pdf').status_code, 422)
        before = self.app['changes'][0]['before']
        after = self.app['changes'][0]['after']
        self.mutate('changes', {'decisions': {'c1': 'rejected'}}, 'put')
        self.mutate('resume-assemble')
        self.assertIn(before, self.app['resume_text'])
        self.assertNotIn(after, self.app['resume_text'])
        self.mutate('changes', {'decisions': {'c1': 'accepted'}}, 'put')
        self.mutate('resume-assemble')
        self.assertIn(after, self.app['resume_text'])
        self.mutate('resume', {'text': self.app['resume_text'], 'confirmed': True}, 'put')
        pdf = self.client.get(self.url + '/export/resume.pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertIn('FastAPI', ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages))
        docx = self.client.get(self.url + '/export/resume.docx')
        self.assertEqual(docx.content[:2], b'PK')
        self.mutate('letter', {'text': self.app['letter_text'], 'confirmed': True}, 'put')
        self.assertEqual(self.client.get(self.url + '/export/letter.txt').status_code, 200)
        self.assertEqual(self.app['stages']['package'], 'done')

    def test_unreviewed_changes_cannot_be_exported_or_assembled(self):
        self.mutate('resume-assemble', expected=422)
        self.mutate('resume', {'text': 'Manual bypass', 'confirmed': True}, 'put', expected=422)

    def test_profile_edit_invalidates_downstream_results_but_preserves_history(self):
        old_version = self.app['version']
        facts = self.app['facts']
        facts[2]['text'] = 'Python, SQLite'
        self.mutate('profile', {'facts': facts}, 'put')
        self.assertEqual(self.app['findings'], [])
        self.assertEqual(self.app['changes'], [])
        self.assertEqual(self.app['resume_text'], '')
        self.assertFalse(self.app['letter_confirmed'])
        self.assertEqual(self.app['facts'][2]['origin'], 'user')
        self.mutate('restore', {'target_version': old_version})
        self.assertTrue(self.app['findings'])
        self.assertGreater(self.app['version'], old_version)

    def test_stale_tab_cannot_overwrite_new_data(self):
        version = self.app['version']
        self.mutate('interview', {'skip': True}, 'put')
        response = self.client.put(self.url + '/interview', json={'version': version, 'skip': True})
        self.assertEqual(response.status_code, 409)

    def test_interview_confirms_answers_and_replaces_prior_answer(self):
        self.mutate('interview', {'answers': {'q1': 'Работал только с SQLite.'}}, 'put')
        self.assertEqual(self.app['findings'], [])
        self.assertEqual(sum(f['origin'] == 'interview' for f in self.app['facts']), 1)
        self.mutate('interview', {'answers': {'q1': 'SQLite использовал в учебном проекте.'}}, 'put')
        answers = [f for f in self.app['facts'] if f['origin'] == 'interview']
        self.assertEqual(len(answers), 1)
        self.assertIn('SQLite использовал', answers[0]['text'])

    def test_skipped_interview_adds_no_facts(self):
        facts = self.app['facts']
        self.mutate('interview', {'skip': True}, 'put')
        self.assertEqual(self.app['facts'], facts)
        self.assertTrue(self.app['findings'])

    def test_pdf_upload_replaces_source_but_never_keeps_original(self):
        data = Path('demo/resume.pdf').read_bytes()
        response = self.client.post(self.url + '/resume-upload', params={'version': self.app['version']},
                    files={'file': ('../../secret.pdf', data, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn('Алексей', response.json()['resume_source'])
        self.assertFalse(response.json()['profile_confirmed'])
        self.assertFalse(list(Path(self.tmp.name).glob('*.pdf')))

    def test_pdf_limits_scan_encryption_and_invalid_signature(self):
        cases = [(b'not a PDF', 422, 'не является'), (b'%PDF-' + b'x' * documents.MAX_FILE, 413, '10 МБ')]
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        out = io.BytesIO(); writer.write(out)
        cases.append((out.getvalue(), 422, 'OCR'))
        writer.encrypt('secret'); out = io.BytesIO(); writer.write(out)
        cases.append((out.getvalue(), 422, 'паролем'))
        writer = PdfWriter()
        for _ in range(21): writer.add_blank_page(width=100, height=100)
        out = io.BytesIO(); writer.write(out)
        cases.append((out.getvalue(), 422, '20 страниц'))
        for content, status, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(HTTPException) as caught:
                    documents.extract_pdf(content)
                self.assertEqual(caught.exception.status_code, status)
                self.assertIn(message, caught.exception.detail)

    def test_ai_extraction_rejects_missing_evidence_without_mutating_profile(self):
        original = self.app['facts']
        fake = ProfileExtraction(facts=[Fact(section='skills', text='Rust', evidence='not in source')])
        with patch.object(providers, 'structured', new_callable=AsyncMock, return_value=fake):
            self.mutate('profile-extract')
        current = self.client.get(self.url).json()
        self.assertEqual(current['facts'], original)
        self.assertEqual(current['profile_candidates'][0]['text'], 'Rust')
        self.assertEqual(current['runs'][0]['status'], 'done')

    def test_extraction_recovers_exact_source_whitespace_and_drops_unsupported_facts(self):
        self.mutate('resume-source', {'text': 'Навыки: Python,\n FastAPI. Опыт: 2023–2027.'}, 'put')
        result = ProfileExtraction(facts=[
            Fact(id='supported', section='skills', text='Python, FastAPI', evidence='Python, FastAPI'),
            Fact(id='unsupported', section='skills', text='Rust', evidence='Rust')])
        with patch.object(providers, 'structured', new_callable=AsyncMock, return_value=result):
            self.mutate('profile-extract')
        self.assertEqual(len(self.app['facts']), 1)
        self.assertEqual(self.app['facts'][0]['evidence'], 'Python,\n FastAPI')
        self.assertFalse(self.app['profile_confirmed'])
        self.assertTrue(self.app['notices'])

    def test_full_live_pipeline_uses_typed_providers_and_exports(self):
        original = example()
        with patch.object(providers, 'structured', new_callable=AsyncMock) as ai:
            ai.return_value = ProfileExtraction(facts=[Fact(id='f3', section='skills', text='Python, FastAPI, SQLite, Git, pytest, основы SQL.', evidence='Python, FastAPI, SQLite, Git, pytest, основы SQL.')])
            self.mutate('profile-extract')
            self.mutate('profile', {'facts': self.app['facts']}, 'put')
            self.mutate('vacancy-source', {'text': original.vacancy_source}, 'put')
            ai.return_value = original.vacancy
            self.mutate('vacancy-extract')
            self.mutate('vacancy', {'vacancy': self.app['vacancy']}, 'put')
            ai.return_value = MatchResult(findings=[Finding(requirement_id=r.id, status='unknown', fact_ids=[], explanation='Нужны уточнения') for r in original.vacancy.requirements])
            self.mutate('match')
            self.mutate('interview', {'skip': True}, 'put')
            ai.return_value = ChangeResult(changes=[ResumeChange(id='edit1', fact_id='f3', before=self.app['facts'][0]['text'], after='Python · FastAPI · SQLite · Git · pytest · основы SQL', reason='Удобнее прочитать список')])
            self.mutate('changes')
            self.mutate('changes', {'decisions': {'edit1': 'accepted'}}, 'put')
            self.mutate('resume-assemble')
            self.mutate('resume', {'text': self.app['resume_text'], 'confirmed': True}, 'put')
            ai.return_value = LetterResult(text='Здравствуйте! Знаю Python и основы SQL.', fact_ids=['f3'])
            self.mutate('letter-generate')
            self.mutate('letter', {'text': self.app['letter_text'], 'confirmed': True}, 'put')
            self.assertEqual(self.app['mode'], 'live')
            self.assertEqual(self.app['stages']['package'], 'done')
            self.assertEqual(self.client.get(self.url + '/export/resume.pdf').status_code, 200)

    def test_match_must_cover_all_requirements_and_known_facts(self):
        bad = MatchResult(findings=[Finding(requirement_id='r1', status='supported', fact_ids=['invented'], explanation='unsupported')])
        with patch.object(providers, 'structured', new_callable=AsyncMock, return_value=bad):
            self.mutate('match')
        self.assertEqual(len(self.app['findings']), len(self.app['vacancy']['requirements']))
        self.assertTrue(self.app['findings'][0]['needs_review'])
        self.assertEqual(self.app['findings'][0]['status'], 'unknown')

    def test_unconfirmed_work_conditions_cannot_become_supported(self):
        app = example()
        findings = [Finding(requirement_id='r7', status='supported', fact_ids=['f1'],
                            explanation='Does not contradict the CV')]
        conservative_findings(app, findings)
        self.assertEqual(findings[0].status, 'unknown')
        self.assertEqual(findings[0].fact_ids, [])
        duties = [Finding(requirement_id='r6', status='supported', fact_ids=['f4'], explanation='Ready for anything')]
        conservative_findings(app, duties)
        self.assertEqual(duties[0].status, 'partial')
        self.assertNotIn('Ready', duties[0].explanation)

    def test_letter_cannot_invent_hours_or_test_assignment(self):
        for text in ['Готов работать 20 часов в неделю.', 'Готов выполнить тестовое задание.']:
            result = LetterResult(text=text, fact_ids=['f3'])
            with self.subTest(text=text), patch.object(providers, 'structured', new_callable=AsyncMock, return_value=result):
                self.mutate('letter-generate', expected=422)
        self.assertNotIn('20 часов', self.client.get(self.url).json()['letter_text'])

    def test_vacancy_fetch_failure_does_not_invent_data(self):
        with patch.object(providers, 'vacancy_page', new_callable=AsyncMock,
                          side_effect=HTTPException(422, 'Вставьте текст вручную')):
            self.mutate('vacancy-fetch', {'url': 'https://example.org/job'}, expected=422)
        self.assertEqual(self.client.get(self.url).json()['vacancy_source'], self.app['vacancy_source'])

    def test_provider_timeout_preserves_previous_results_and_reports_retry(self):
        with patch.object(providers, 'structured', new_callable=AsyncMock, side_effect=httpx.ConnectTimeout('test timeout')):
            response = self.mutate('letter-generate', expected=504)
        self.assertIn('сохранены', response.json()['detail'])
        current = self.client.get(self.url).json()
        self.assertEqual(current['letter_text'], self.app['letter_text'])
        self.assertEqual(current['runs'][0]['status'], 'failed')

    def test_manual_requirement_requires_real_quote(self):
        vacancy = self.app['vacancy']
        vacancy['requirements'][0]['evidence'] = 'Invented requirement'
        self.mutate('vacancy', {'vacancy': vacancy}, 'put', expected=422)

    def test_research_searches_only_company_and_validates_source_ids(self):
        from career import research
        sources = [{'id':'S01', 'title':'Official', 'url':'https://example.org', 'published_date':'',
                    'snippet':'Листва Лаб создаёт инструменты для автоматизации работы небольших команд.', 'topics':['overview'], 'retrieved_at':'2026-10-26'}]
        result = ResearchDraft(claims=[ResearchPoint(text='Company fact', category='overview', evidence_ids=['S01-E1'])])
        with patch.object(research, 'collect_sources', new_callable=AsyncMock,
                          return_value=research.CollectedSources(sources=sources)) as search, \
             patch.object(providers, 'structured', new_callable=AsyncMock, return_value=result):
            self.mutate('research')
        self.assertEqual(search.call_args.args[0], 'Листва Лаб')
        self.assertNotIn('alexey', str(search.call_args))
        self.assertTrue(self.app['sources'][0]['retrieved_at'])
        self.assertEqual(len(self.app['research']['coverage']), 6)
        self.assertFalse(self.app['letter_confirmed'])

    def test_cross_site_mutation_rejected_and_private_responses_not_cached(self):
        r = self.client.post('/api/applications', json={}, headers={'Origin': 'https://evil.example'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(self.url).headers['cache-control'], 'no-store')

    def test_delete_removes_versions_and_data(self):
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/versions').status_code, 404)


class URLTests(unittest.TestCase):
    def test_evidence_normalization_never_removes_words_or_changes_numbers(self):
        self.assertEqual(anchor_quote('2023-2027', 'Учёба: 2023–2027.'), '2023–2027')
        self.assertEqual(anchor_quote('Коммерческий опыт', 'Коммерческого опыта нет'), '')
        self.assertEqual(anchor_quote('25 проектов', '2 проекта'), '')

    def test_typed_contracts_are_accepted_by_gigachat_without_network(self):
        from langchain_gigachat import GigaChat
        from career.schemas import Vacancy
        model = GigaChat(credentials='test')
        for schema in [ProfileExtraction, Vacancy, MatchResult, ChangeResult, LetterResult, ResearchResult, ResearchPlan, ResearchDraft]:
            with self.subTest(schema=schema.__name__):
                model.with_structured_output(schema)

    def test_public_url_blocks_local_dns_private_ipv6_credentials_and_ports(self):
        for url in ['http://localhost', 'file:///tmp/file', 'http://x.local', 'https://user:pass@example.org', 'https://example.org:8080']:
            with self.subTest(url=url), self.assertRaises(HTTPException): providers.public_url(url)
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1']:
            with patch.object(socket, 'getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                with self.subTest(address=address), self.assertRaises(HTTPException): providers.public_url('https://public-looking.example')

    def test_public_url_normalizes_fragment(self):
        with patch.object(socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(providers.public_url('https://example.org/job#x'), 'https://example.org/job')
