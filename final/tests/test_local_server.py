"""The current browser entry point works without legacy agents or provider calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import web_app
from career import providers, storage


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'SBER21_DB':str(Path(self.folder.name)/'test.sqlite'),
                                      'GIGACHAT_CREDENTIALS':'','TAVILY_API_KEY':''})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.folder.cleanup()

    def test_offline_browser_bootstrap_and_demo_have_no_provider_dependency(self):
        with patch.object(providers,'structured',AsyncMock(side_effect=AssertionError('No AI in demo'))), TestClient(web_app.app,base_url='http://localhost') as client:
            health=client.get('/api/health').json()
            self.assertFalse(health['gigachat_configured'])
            self.assertFalse(health['tavily_configured'])
            page=client.get('/')
            self.assertEqual(page.status_code,200)
            soup=BeautifulSoup(page.text,'html.parser')
            for node in soup.select('script[src],link[href]'):
                url=node.get('src') or node['href']
                self.assertEqual(client.get(url).status_code,200,url)
            demo=client.post('/api/applications',json={'demo':True})
            self.assertEqual(demo.status_code,201)
            self.assertTrue(demo.json()['profile_confirmed'])
            self.assertEqual(demo.json()['mode'],'example')

    def test_server_start_marks_unfinished_work_interrupted(self):
        storage.record_run('interrupted-example','synthetic','match','running')
        with TestClient(web_app.app,base_url='http://localhost'):
            self.assertEqual(storage.runs('synthetic')[0]['status'],'interrupted')

    def test_unused_legacy_routes_are_not_exposed(self):
        with TestClient(web_app.app,base_url='http://localhost') as client:
            self.assertEqual(client.post('/api/chat',json={}).status_code,404)
            self.assertEqual(client.get('/api/download/unused').status_code,404)
