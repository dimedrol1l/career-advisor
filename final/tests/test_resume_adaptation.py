"""Deeper editing can combine evidence, without silently inventing metrics."""
import unittest
from unittest.mock import AsyncMock, patch
from career import providers, workflows, documents
from career.demo import example
from career.schemas import Fact, ChangeResult, ResumeChange
from career.resume_strategy import RESUME_ADAPTATION_PROMPT


class ResumeAdaptationTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        app=example()
        app.facts=[Fact(id='intro',section='summary',text='Студент. Интересует автоматизация.'),
                   Fact(id='work',section='experience',text='Обработал 20 ошибок в корпоративных информационных системах.'),
                   Fact(id='project',section='projects',text='Разработал прототип на Python. Исследую API сервиса.'),
                   Fact(id='skills',section='skills',text='Python и основы SQL.'),
                   Fact(id='contacts',section='contacts',text='synthetic@example.com')]
        return app

    async def run_edits(self,app,changes,**kwargs):
        with patch.object(providers,'structured',AsyncMock(return_value=ChangeResult(changes=changes,**kwargs))) as ai:
            await workflows.propose_changes(app)
        return ai

    async def test_summary_can_use_explicit_additional_evidence(self):
        app=self.app()
        after='Студент с опытом работы с корпоративными системами: обработал 20 ошибок. Разработал прототип на Python; исследую API сервиса.'
        change=ResumeChange(fact_id='F01',supporting_fact_ids=['F02','F03'],before=app.facts[0].text,
                            after=after,reason='Связывает реальный опыт с задачами автоматизации.')
        ai=await self.run_edits(app,[change])
        self.assertEqual(app.changes[0].supporting_fact_ids,['work','project'])
        self.assertEqual(app.changes[0].decision,'pending')
        self.assertEqual(ai.call_args.args[1],RESUME_ADAPTATION_PROMPT)
        self.assertNotIn(after,documents.assemble_resume(app))
        app.changes[0].decision='accepted'
        self.assertIn(after,documents.assemble_resume(app))

    async def test_skills_can_use_project_evidence_without_new_metric(self):
        app=self.app()
        change=ResumeChange(fact_id='F04',supporting_fact_ids=['F03'],before=app.facts[3].text,
                            after='Python — прототип прикладной программы; основы SQL.',reason='Привязывает навык к проекту.')
        await self.run_edits(app,[change])
        self.assertEqual(len(app.changes),1)
        self.assertEqual(app.changes[0].supporting_fact_ids,['project'])

    async def test_different_project_cannot_donate_achievements(self):
        app=self.app()
        change=ResumeChange(fact_id='F03',supporting_fact_ids=['F02'],before=app.facts[2].text,
                            after='В Python-прототипе исправил 20 ошибок.',reason='Неправильный перенос результата.')
        await self.run_edits(app,[change])
        self.assertEqual(app.changes,[])

    async def test_contacts_and_unknown_facts_cannot_support_summary(self):
        for ref in ['F05','F999']:
            app=self.app()
            change=ResumeChange(fact_id='F01',supporting_fact_ids=[ref],before=app.facts[0].text,
                                after='Студент. Создаю системы автоматизации.',reason='Недостоверное основание.')
            await self.run_edits(app,[change])
            self.assertEqual(app.changes,[])

    async def test_count_cannot_become_percent_improvement(self):
        app=self.app()
        change=ResumeChange(fact_id='F02',before=app.facts[1].text,
                            after='Повысил качество систем на 20%.',reason='Выдуманный результат из известного числа.')
        await self.run_edits(app,[change])
        self.assertEqual(app.changes,[])
        self.assertTrue(app.stage_notices['changes'])

    async def test_existing_percentage_allows_equivalent_notation(self):
        app=self.app();app.facts[1].text='По измерению сократил время операции на 20 процентов.'
        change=ResumeChange(fact_id='F02',before=app.facts[1].text,
                            after='Сократил время операции на 20% по результатам измерения.',reason='Коротко описывает измеренный результат.')
        await self.run_edits(app,[change])
        self.assertEqual(len(app.changes),1)

    async def test_section_order_keeps_contacts_and_only_existing_sections(self):
        app=self.app()
        await self.run_edits(app,[],section_order=['projects','projects','education','skills','contacts'],order_reason='Проекты полезнее общих формулировок.')
        self.assertEqual(app.section_order,['contacts','projects','skills'])
        self.assertTrue(documents.assemble_resume(app).startswith('## Контакты'))

    async def test_rejected_rewrite_leaves_original_facts_unchanged(self):
        app=self.app();before=app.model_copy(deep=True)
        change=ResumeChange(fact_id='F03',before=app.facts[2].text,
                            after='• Разработал Python-прототип.\n• Исследую API сервиса.',reason='Выделяет личную разработку и текущую стадию проекта.')
        await self.run_edits(app,[change]);app.changes[0].decision='rejected'
        self.assertEqual(app.facts,before.facts)
        self.assertIn(before.facts[2].text,documents.assemble_resume(app))
        self.assertNotIn('• Разработал',documents.assemble_resume(app))
