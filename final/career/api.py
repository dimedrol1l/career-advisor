"""Local-only application API. External work runs only on explicit user action."""
import asyncio
import logging
import secrets
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field, ValidationError
import app_config
from career import storage, workflows, providers, documents
from career.schemas import Application, Contract, Fact, Vacancy, Finding

router = APIRouter(prefix='/api/applications')
logger = logging.getLogger('sber21.workflow')
_busy: set[str] = set()


class NewApplication(Contract):
    title: str = Field(default='Новый отклик', max_length=200)
    demo: bool = False


class Edit(Contract):
    version: int = Field(ge=1)


class SourceEdit(Edit):
    text: str = Field(min_length=1, max_length=60000)


class ProfileEdit(Edit):
    facts: list[Fact] = Field(min_length=1, max_length=100)


class VacancyEdit(Edit):
    vacancy: Vacancy


class UrlEdit(Edit):
    url: str = Field(max_length=2048)


class InterviewEdit(Edit):
    answers: dict[str, str] = Field(default_factory=dict, max_length=4)
    skip: bool = False


class Decisions(Edit):
    decisions: dict[str, Literal['accepted', 'rejected', 'pending']] = Field(max_length=50)
    use_suggested_order: bool = True


class DocumentEdit(Edit):
    text: str = Field(min_length=1, max_length=60000)
    confirmed: bool = False


class LetterOptions(Edit):
    tone: Literal['professional', 'warm', 'concise'] = 'professional'
    length: Literal['short', 'standard'] = 'standard'


class ResearchOptions(Edit):
    website: str = Field(default="", max_length=2048)
    reuse_sources: bool = False


class FindingEdit(Edit):
    finding: Finding


class RestoreEdit(Edit):
    target_version: int = Field(ge=1)


def view(app):
    return {**app.model_dump(), 'stages': workflows.stages(app), 'runs': storage.runs(app.id)}


async def mutate(application_id, edit, action, callback):
    if application_id in _busy:
        raise HTTPException(409, 'Для этого отклика уже выполняется операция. Дождитесь завершения.')
    _busy.add(application_id)
    request_id = secrets.token_hex(8)
    started = False
    succeeded = False
    try:
        app = storage.get(application_id)
        if app.version != edit.version:
            raise HTTPException(409, 'Отклик изменился. Обновите страницу и повторите действие.')
        storage.record_run(request_id, application_id, action, 'running')
        started = True
        await callback(app)
        provenance = {'profile_extract': ('profile', 'ai'), 'profile_confirm': ('profile', 'user'),
                      'vacancy_extract': ('vacancy', 'ai'), 'vacancy_confirm': ('vacancy', 'user'),
                      'match': ('match', 'ai'), 'match_review': ('match', 'user'), 'changes': ('changes', 'ai'), 'resume_assemble': ('resume', 'assembled'),
                      'resume_save': ('resume', 'user'), 'letter_generate': ('letter', 'ai'),
                      'letter_save': ('letter', 'user'), 'research': ('research', 'ai'), 'interview': ('interview', 'user')}
        if action in provenance:
            stage, origin = provenance[action]
            app.provenance[stage] = origin
        result = storage.save(app, edit.version)
        storage.record_run(request_id, application_id, action, 'done')
        succeeded = True
        logger.info('workflow_complete request_id=%s stage=%s', request_id, action)
        return view(result)
    except HTTPException:
        raise
    except app_config.ConfigurationError:
        raise HTTPException(503, 'GigaChat не настроен. Проверьте локальную конфигурацию. Ваши данные сохранены.') from None
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(504, 'Внешний сервис не ответил вовремя. Предыдущие результаты сохранены; повторите позже.') from None
    except httpx.HTTPStatusError as exc:
        code = 429 if exc.response.status_code in {429, 432} else 502
        raise HTTPException(code, 'Провайдер ограничил запрос или временно недоступен. Повторите позже; можно продолжить вручную.') from None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(502, 'AI вернул некорректный результат. Данные не изменены. Повторите действие или заполните вручную.') from None
    except Exception as exc:
        logger.warning('workflow_failed request_id=%s stage=%s error_type=%s', request_id, action, type(exc).__name__)
        raise HTTPException(502, 'Не удалось выполнить операцию. Предыдущие результаты сохранены.') from None
    finally:
        if started and not succeeded:
            storage.record_run(request_id, application_id, action, 'failed')
        _busy.discard(application_id)


@router.get('')
async def list_apps():
    return [{'id': a.id, 'title': a.title, 'company': a.vacancy.company, 'demo': a.demo, 'mode': a.mode,
             'updated_at': a.updated_at, 'stages': workflows.stages(a)} for a in storage.all_applications()]


@router.post('', status_code=201)
async def create(edit: NewApplication):
    if edit.demo:
        from career.demo import example
        app = example()
    else:
        app = Application(title=edit.title.strip() or 'Новый отклик')
    return view(storage.save(app))


@router.get('/{application_id}')
async def read(application_id: str):
    return view(storage.get(application_id))


@router.delete('/{application_id}', status_code=204)
async def delete(application_id: str):
    if application_id in _busy:
        raise HTTPException(409, 'Дождитесь завершения операции перед удалением.')
    storage.delete(application_id)
    return Response(status_code=204)


@router.get('/{application_id}/versions')
async def versions(application_id: str):
    return storage.versions(application_id)


@router.post('/{application_id}/restore')
async def restore(application_id: str, edit: RestoreEdit):
    if application_id in _busy:
        raise HTTPException(409, 'Дождитесь завершения операции.')
    return view(storage.restore(application_id, edit.target_version, edit.version))


@router.post('/{application_id}/resume-upload')
async def upload(application_id: str, request: Request, version: int):
    # Cap the request stream before the multipart parser can spool arbitrary input
    # to disk. The uploaded filename is never used as a filesystem path.
    from starlette.requests import Request as StarletteRequest
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > documents.MAX_FILE + 65536:
            raise HTTPException(413, 'PDF превышает 10 МБ.')
    sent = False
    async def receive():
        nonlocal sent
        if sent:
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        sent = True
        return {'type': 'http.request', 'body': bytes(data), 'more_body': False}
    bounded = StarletteRequest(request.scope, receive)
    async with bounded.form(max_files=1, max_fields=0, max_part_size=documents.MAX_FILE) as form:
        file = form.get('file')
        from starlette.datastructures import UploadFile as StarletteUploadFile
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(422, 'Выберите PDF-файл.')
        if file.content_type not in {'application/pdf', 'application/octet-stream'}:
            raise HTTPException(422, 'Поддерживаются только PDF-файлы.')
        content = await file.read(documents.MAX_FILE + 1)
    async def apply(app):
        pages = await asyncio.to_thread(documents.extract_pdf, content)
        workflows.invalidate(app, 'profile')
        app.resume_pages = pages
        app.resume_source = '\n\n'.join(pages)
        app.facts = []
        app.profile_candidates = []
        app.notices = []
        app.stage_notices['profile'] = []
        app.profile_confirmed = False
    return await mutate(application_id, Edit(version=version), 'resume_upload', apply)


@router.put('/{application_id}/resume-source')
async def resume_source(application_id: str, edit: SourceEdit):
    async def apply(app):
        workflows.invalidate(app, 'profile')
        app.resume_source, app.resume_pages = edit.text, []
        app.facts, app.profile_confirmed = [], False
        app.profile_candidates = []
        app.notices = []
        app.stage_notices['profile'] = []
    return await mutate(application_id, edit, 'resume_source', apply)


@router.post('/{application_id}/profile-extract')
async def profile_extract(application_id: str, edit: Edit):
    async def apply(app):
        await workflows.extract_profile(app)
        app.mode = 'live'
    return await mutate(application_id, edit, 'profile_extract', apply)


@router.put('/{application_id}/profile')
async def profile(application_id: str, edit: ProfileEdit):
    async def apply(app):
        workflows.unique_ids(edit.facts)
        old = {f.id: f for f in app.facts}
        for fact in edit.facts:
            previous = old.get(fact.id)
            # Client cannot claim that manually entered facts came from a PDF.
            if previous and fact.text == previous.text and fact.section == previous.section:
                fact.evidence, fact.page, fact.origin = previous.evidence, previous.page, previous.origin
            else:
                fact.evidence, fact.page, fact.origin = fact.text, None, 'user'
        workflows.invalidate(app, 'profile')
        app.facts, app.profile_confirmed = edit.facts, True
        app.profile_candidates = [f for f in app.profile_candidates if f.id not in {x.id for x in edit.facts}]
        app.notices = []
        app.stage_notices['profile'] = []
    return await mutate(application_id, edit, 'profile_confirm', apply)


@router.put('/{application_id}/vacancy-source')
async def vacancy_source(application_id: str, edit: SourceEdit):
    async def apply(app):
        workflows.invalidate(app, 'vacancy')
        app.vacancy_source, app.vacancy_url = edit.text, ''
        app.vacancy, app.vacancy_confirmed = Vacancy(), False
    return await mutate(application_id, edit, 'vacancy_source', apply)


@router.post('/{application_id}/vacancy-fetch')
async def vacancy_fetch(application_id: str, edit: UrlEdit):
    async def apply(app):
        url, text = await providers.vacancy_page(edit.url)
        workflows.invalidate(app, 'vacancy')
        app.vacancy_url, app.vacancy_source = url, text
        app.vacancy, app.vacancy_confirmed = Vacancy(), False
        app.mode = 'live'
    return await mutate(application_id, edit, 'vacancy_fetch', apply)


@router.post('/{application_id}/vacancy-extract')
async def vacancy_extract(application_id: str, edit: Edit):
    async def apply(app):
        await workflows.extract_vacancy(app)
        app.mode = 'live'
    return await mutate(application_id, edit, 'vacancy_extract', apply)


@router.put('/{application_id}/vacancy')
async def vacancy(application_id: str, edit: VacancyEdit):
    async def apply(app):
        workflows.validate_vacancy(edit.vacancy, app.vacancy_source)
        workflows.invalidate(app, 'vacancy')
        app.vacancy, app.vacancy_confirmed = edit.vacancy, True
        app.title = ' · '.join(x for x in [app.vacancy.title, app.vacancy.company] if x) or 'Новый отклик'
    return await mutate(application_id, edit, 'vacancy_confirm', apply)


@router.post('/{application_id}/match')
async def match(application_id: str, edit: Edit):
    async def apply(app):
        await workflows.match(app)
        app.mode = 'live'
    return await mutate(application_id, edit, 'match', apply)


@router.put('/{application_id}/interview')
async def interview(application_id: str, edit: InterviewEdit):
    async def apply(app):
        workflows.check_inputs(app)
        known = {q.id for q in app.questions}
        workflows.require(set(edit.answers) <= known, 'Неизвестный вопрос интервью.')
        if edit.skip:
            for question in app.questions:
                if not question.answer:
                    question.skipped = True
        else:
            added = False
            for question in app.questions:
                answer = edit.answers.get(question.id, '').strip()
                workflows.require(len(answer) <= 3000, 'Ответ не должен превышать 3000 символов.')
                if answer and answer != question.answer:
                    # Update, rather than duplicate, a previously confirmed answer.
                    fact_id = 'answer-' + question.id
                    app.facts = [f for f in app.facts if f.id != fact_id]
                    requirement = next(r for r in app.vacancy.requirements if r.id == question.requirement_id)
                    app.facts.append(Fact(id=fact_id, section='summary' if requirement.category == 'condition' else 'experience',
                                          text=f'Уточнение по «{requirement.text}»: {answer}',
                                          evidence=answer, origin='interview'))
                    question.answer, question.skipped = answer, False
                    added = True
            workflows.require(len(app.facts) <= 100, 'В профиле больше 100 фактов. Объедините связанные факты перед добавлением ответов.')
            if added:
                workflows.invalidate(app, 'interview')
        app.interview_done = True
    return await mutate(application_id, edit, 'interview', apply)


@router.post('/{application_id}/changes')
async def changes(application_id: str, edit: Edit):
    async def apply(app):
        await workflows.propose_changes(app)
        app.mode = 'live'
    return await mutate(application_id, edit, 'changes', apply)


@router.put('/{application_id}/changes')
async def decisions(application_id: str, edit: Decisions):
    async def apply(app):
        workflows.require(app.changes_generated, 'Сначала получите предложения правок.')
        workflows.require(set(edit.decisions) <= {c.id for c in app.changes}, 'Неизвестная правка.')
        workflows.invalidate(app, 'changes')
        for change in app.changes:
            change.decision = edit.decisions.get(change.id, change.decision)
        if not edit.use_suggested_order:
            app.section_order = []
            app.order_reason = ''
    return await mutate(application_id, edit, 'change_decisions', apply)


@router.post('/{application_id}/resume-assemble')
async def assemble(application_id: str, edit: Edit):
    async def apply(app):
        workflows.check_inputs(app)
        workflows.require(not any(c.decision == 'pending' for c in app.changes), 'Примите или отклоните каждую предложенную правку.')
        app.resume_text = documents.assemble_resume(app)
        app.resume_confirmed = False
    return await mutate(application_id, edit, 'resume_assemble', apply)


@router.put('/{application_id}/resume')
async def resume_edit(application_id: str, edit: DocumentEdit):
    async def apply(app):
        workflows.check_inputs(app)
        workflows.require(not any(c.decision == 'pending' for c in app.changes), 'Сначала рассмотрите все правки.')
        app.resume_text, app.resume_confirmed = edit.text, edit.confirmed
    return await mutate(application_id, edit, 'resume_save', apply)


@router.post('/{application_id}/letter-generate')
async def letter_generate(application_id: str, edit: LetterOptions):
    async def apply(app):
        await workflows.generate_letter(app, edit.tone, edit.length)
        app.mode = 'live'
    return await mutate(application_id, edit, 'letter_generate', apply)


@router.put('/{application_id}/letter')
async def letter_edit(application_id: str, edit: DocumentEdit):
    async def apply(app):
        workflows.check_inputs(app)
        workflows.require(len(edit.text) <= 12000, 'Письмо не должно превышать 12000 символов.')
        app.letter_text, app.letter_confirmed = edit.text, edit.confirmed
        app.stage_notices['letter'] = []
    return await mutate(application_id, edit, 'letter_save', apply)


@router.post('/{application_id}/research')
async def research(application_id: str, edit: ResearchOptions):
    async def apply(app):
        from career.research import run_research
        workflows.require(app.vacancy_confirmed and bool(app.vacancy.company), 'Сначала подтвердите название компании в вакансии.')
        await run_research(app, edit.website.strip(), edit.reuse_sources)
    return await mutate(application_id, edit, 'research', apply)


@router.put('/{application_id}/match-review')
async def match_review(application_id: str, edit: FindingEdit):
    async def apply(app):
        finding = edit.finding
        workflows.check_inputs(app)
        workflows.require(finding.requirement_id in {f.requirement_id for f in app.findings}, 'Сначала выполните анализ этого требования.')
        workflows.require(set(finding.fact_ids) <= {f.id for f in app.facts}, 'Выберите существующие факты профиля.')
        workflows.require(finding.status == 'unknown' or bool(finding.fact_ids), 'Для этого вывода выберите факты-основания; иначе оставьте «Нет данных».')
        finding.needs_review = False
        workflows.conservative_findings(app, [finding])
        workflows.invalidate(app, 'match')
        app.findings = [finding if f.requirement_id == finding.requirement_id else f for f in app.findings]
        count = sum(f.needs_review for f in app.findings)
        app.stage_notices['match'] = [f'Требуют проверки ещё {count} выводов.'] if count else []
    return await mutate(application_id, edit, 'match_review', apply)


@router.get('/{application_id}/export/{kind}')
async def export(application_id: str, kind: Literal['resume.pdf', 'resume.docx', 'letter.txt', 'company.md']):
    app = storage.get(application_id)
    if kind == 'company.md':
        from career.research import report_markdown
        content, media = report_markdown(app).encode('utf-8'), 'text/markdown; charset=utf-8'
    elif kind == 'letter.txt':
        workflows.require(app.letter_confirmed and bool(app.letter_text), 'Проверьте и подтвердите письмо перед экспортом.')
        content, media = app.letter_text.encode('utf-8'), 'text/plain; charset=utf-8'
    else:
        workflows.require(app.resume_confirmed and bool(app.resume_text), 'Проверьте и подтвердите резюме перед экспортом.')
        if kind.endswith('.pdf'):
            content = await asyncio.to_thread(documents.pdf_bytes, app.resume_text)
            media = 'application/pdf'
        else:
            content = await asyncio.to_thread(documents.docx_bytes, app.resume_text)
            media = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="sber21-{kind}"', 'Cache-Control': 'no-store'})
