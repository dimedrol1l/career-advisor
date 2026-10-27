"""Recovery and research contracts, with synthetic inputs and no provider network."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
import web_app
from career import providers, workflows, research
from career.demo import example
from career.evidence import anchor_quote, catalog, resolve_ref
from career.schemas import Fact, ProfileExtraction, Finding, MatchResult, ChangeResult, ResumeChange, LetterResult, ResearchResult, Claim, ResearchDraft, ResearchPoint


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_split_letters_anchor_to_raw_text_but_not_digits(self):
        self.assertEqual(anchor_quote('PostgreSQL / SQL · Django', 'Post greSQL / SQL · Dj an g o'), 'Post greSQL / SQL · Dj an g o')
        self.assertEqual(anchor_quote('20 проектов', '2 0 проектов'), '')
        self.assertEqual(anchor_quote('Нет опыта Python', 'Нет опыта Py thon'), 'Нет опыта Py thon')

    async def test_short_references_do_not_guess_unknown_ids(self):
        app = example(); aliases, data = catalog(app.facts, 'F')
        self.assertEqual(data[0]['id'], 'F01')
        self.assertEqual(resolve_ref('[F1]', {f.id for f in app.facts}, aliases, 'F'), app.facts[0].id)
        self.assertIsNone(resolve_ref('invented', {f.id for f in app.facts}, aliases, 'F'))

    async def test_source_reference_recovers_missing_quote_and_repairs_duplicate_ids(self):
        app = example(); app.resume_source = 'Навыки: Post greSQL и Py thon.'
        output = ProfileExtraction(facts=[Fact(id='duplicate', section='skills', text='PostgreSQL', source_refs=['T01']),
                                          Fact(id='duplicate', section='skills', text='Python', evidence='Python')])
        with patch.object(providers, 'structured', AsyncMock(return_value=output)):
            await workflows.extract_profile(app)
        self.assertEqual(len(app.facts), 2)
        self.assertEqual(len({f.id for f in app.facts}), 2)
        self.assertEqual(app.facts[0].evidence, app.resume_source)
        self.assertFalse(app.profile_confirmed)

    async def test_unverified_candidates_preserve_previous_confirmed_profile(self):
        app = example(); old = app.facts.copy()
        output = ProfileExtraction(facts=[Fact(section='skills', text='Rust', source_refs=['T999'])])
        with patch.object(providers, 'structured', AsyncMock(return_value=output)):
            await workflows.extract_profile(app)
        self.assertEqual(app.facts, old)
        self.assertTrue(app.profile_confirmed)
        self.assertEqual(app.profile_candidates[0].text, 'Rust')
        self.assertNotIn('Rust', str(catalog(app.facts, 'F')[1]))

    async def test_invalid_finding_does_not_destroy_valid_finding(self):
        app = example()
        output = MatchResult(findings=[Finding(requirement_id='R01', status='supported', fact_ids=['F03'], explanation='Python'),
                                       Finding(requirement_id='R02', status='supported', fact_ids=['F999'], explanation='Unsupported')])
        with patch.object(providers, 'structured', AsyncMock(return_value=output)):
            await workflows.match(app)
        self.assertEqual(app.findings[0].status, 'supported')
        self.assertFalse(app.findings[0].needs_review)
        self.assertTrue(app.findings[1].needs_review)
        self.assertEqual(len(app.findings), len(app.vacancy.requirements))
        self.assertEqual(workflows.stages(app)['match'], 'review')

    async def test_duplicate_finding_needs_review(self):
        app = example(); f = Finding(requirement_id='R01', status='unknown', explanation='Unknown')
        with patch.object(providers, 'structured', AsyncMock(return_value=MatchResult(findings=[f, f.model_copy()]))):
            await workflows.match(app)
        self.assertTrue(app.findings[0].needs_review)

    async def test_changes_keep_valid_suggestion_beside_invalid_reference(self):
        app = example(); fact = app.facts[2]
        output = ChangeResult(changes=[ResumeChange(fact_id='F03', before=fact.text, after=fact.text, reason='Format'),
                                      ResumeChange(fact_id='F999', before='Invented', after='Invented', reason='Bad')])
        with patch.object(providers, 'structured', AsyncMock(return_value=output)):
            await workflows.propose_changes(app)
        self.assertEqual(len(app.changes), 1)
        self.assertEqual(app.changes[0].fact_id, fact.id)
        self.assertTrue(app.stage_notices['changes'])

    async def test_unknown_letter_reference_saves_unconfirmed_draft(self):
        app = example()
        with patch.object(providers, 'structured', AsyncMock(return_value=LetterResult(text='Здравствуйте! Знаю Python.', fact_ids=['F03', 'F999']))):
            await workflows.generate_letter(app, 'professional', 'short')
        self.assertTrue(app.letter_text)
        self.assertFalse(app.letter_confirmed)
        self.assertEqual(app.letter_fact_ids, ['f3'])
        self.assertTrue(app.stage_notices['letter'])

    async def test_research_partial_failure_retains_sources_and_can_retry_without_search(self):
        app = example(); source = {'id':'S01', 'url':'https://example.org/', 'title':'Компания Листва Лаб · Synthetic', 'snippet':'Листва Лаб создаёт инструменты на Python для автоматизации работы небольших команд.', 'topics':['technology']}
        with patch.object(research, 'collect_sources', AsyncMock(return_value=research.CollectedSources(sources=[source]))), \
             patch.object(providers, 'structured', AsyncMock(side_effect=httpx.ReadTimeout('synthetic'))):
            await research.run_research(app)
        self.assertEqual(app.sources, [source])
        self.assertTrue(app.research.summary_pending)
        output = ResearchDraft(claims=[ResearchPoint(text='Tools', kind='fact', category='technology', evidence_ids=['S01-E1'])])
        with patch.object(research, 'collect_sources', AsyncMock(side_effect=AssertionError('new search'))), \
             patch.object(providers, 'structured', AsyncMock(return_value=output)) as ai:
            await research.run_research(app, reuse_sources=True)
        self.assertFalse(app.research.summary_pending)
        self.assertEqual(len(app.research.claims), 1)
        sent = ai.call_args.args[2]
        self.assertNotIn('facts', sent)
        self.assertNotIn('resume_source', sent)
        self.assertEqual(next(c.status for c in app.research.coverage if c.category=='technology'), 'sourced')

    async def test_research_drops_unknown_sources_and_unanchored_claims(self):
        app = example(); app.sources = [{'id':'S01', 'title':'Листва Лаб', 'snippet':'Листва Лаб создаёт инструменты для автоматизации работы небольших команд.', 'topics':['overview']}]
        output = ResearchDraft(claims=[ResearchPoint(text='Bad', category='overview', evidence_ids=['S999-E1']),
                                     ResearchPoint(text='Bad quote', category='overview', evidence_ids=['S01-E99'])])
        with patch.object(providers, 'structured', AsyncMock(return_value=output)):
            await research.run_research(app, reuse_sources=True)
        self.assertEqual(app.research.claims, [])
        self.assertEqual(len(app.sources), 1)
        self.assertTrue(app.stage_notices['research'])

    async def test_empty_refresh_preserves_previous_research(self):
        app = example(); previous = app.research.model_copy(deep=True)
        with patch.object(research, 'collect_sources', AsyncMock(return_value=research.CollectedSources(failed_topics=list(research.TOPICS)))):
            await research.run_research(app)
        self.assertEqual(app.research, previous)
        self.assertTrue(app.sources)

    async def test_research_collection_is_bounded_diverse_and_handles_extract_failure(self):
        async def search(client, key, topic, query):
            if topic == 'team':
                raise httpx.ReadTimeout('synthetic')
            return {'results':[{'url':f'https://example.org/{topic}/{i}', 'title':'Synthetic Company', 'content':f'Synthetic Company {topic} evidence about products and clients of this synthetic business.'} for i in range(4)] + [{'url':'http://127.0.0.1/'}]}
        with patch.dict(os.environ, {'TAVILY_API_KEY':'synthetic'}), \
             patch.object(research, '_search', AsyncMock(side_effect=search)) as searches, \
             patch.object(research, '_extract', AsyncMock(side_effect=httpx.ReadTimeout('synthetic'))) as extracts:
            result = await research.collect_sources('Synthetic Company')
        self.assertEqual(searches.await_count, 6)
        self.assertEqual(extracts.await_count, 2)
        self.assertEqual(len(result.sources), 12)
        self.assertIn('team', result.failed_topics)
        self.assertTrue(result.warnings)
        self.assertEqual({s['topics'][0] for s in result.sources}, set(research.TOPICS)-{'team'})
        self.assertTrue(all(len(c.args[2])<=6 for c in extracts.call_args_list))

    async def test_duplicate_source_covers_multiple_topics(self):
        output = {'results':[{'url':'https://example.org/same#fragment', 'content':'Synthetic company supplies public evidence about products and clients in this synthetic business.'}]}
        with patch.dict(os.environ, {'TAVILY_API_KEY':'synthetic'}), \
             patch.object(research, '_search', AsyncMock(return_value=output)), \
             patch.object(research, '_extract', AsyncMock(return_value={'results':[], 'failed_results':[{}]})):
            result = await research.collect_sources('Synthetic')
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(set(result.sources[0]['topics']), set(research.TOPICS))
        self.assertEqual(result.sources[0]['published_date'], '')
        self.assertFalse(result.sources[0]['extracted'])

    async def test_private_source_urls_are_filtered(self):
        for url in ['http://localhost/a','http://192.168.0.1/','http://[::1]/','https://user:secret@example.org','file:///tmp/data','https://example.org:9000']:
            self.assertEqual(research.safe_source_url(url), '')


class ManualRecoveryTests(unittest.TestCase):
    def test_manual_review_validates_proof_and_invalidates_documents(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'SBER21_DB':str(Path(folder)/'db.sqlite')}), TestClient(web_app.app, base_url='http://localhost') as client:
            app = client.post('/api/applications', json={'demo':True}).json()
            url = '/api/applications/'+app['id']+'/match-review'
            finding = {'requirement_id':'r1', 'status':'supported', 'fact_ids':[], 'explanation':'My review'}
            bad = client.put(url, json={'version':app['version'], 'finding':finding})
            self.assertEqual(bad.status_code, 422)
            finding['fact_ids']=['f3']
            good = client.put(url, json={'version':app['version'], 'finding':finding})
            self.assertEqual(good.status_code, 200)
            result = good.json()
            self.assertEqual(result['resume_text'], '')
            self.assertEqual(result['letter_text'], '')
            self.assertFalse(result['findings'][0]['needs_review'])
            self.assertEqual(result['provenance']['match'], 'user')
