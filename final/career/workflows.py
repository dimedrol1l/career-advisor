"""Business rules and evidence boundaries, independent of HTTP handlers."""
import re
from fastapi import HTTPException
from career import providers
from career.resume_strategy import RESUME_ADAPTATION_PROMPT
from career.evidence import normalized_evidence, anchor_quote, source_spans, catalog, resolve_ref
from career.schemas import ProfileExtraction, Vacancy, MatchResult, Question, ChangeResult, LetterResult, ResearchResult, Finding, uid


def require(condition, message):
    if not condition:
        raise HTTPException(422, message)


def unique_ids(items):
    require(len({x.id for x in items}) == len(items), 'Обнаружены повторяющиеся идентификаторы. Повторите обработку.')



def invalidate(app, stage):
    if stage in {'profile', 'vacancy'}:
        app.provenance.pop(stage, None)
        app.provenance.pop('interview', None)
    for key in ('match', 'changes', 'resume', 'letter') if stage in {'profile', 'vacancy', 'interview', 'match'} else ('resume',):
        app.provenance.pop(key, None)
        app.stage_notices.pop(key, None)
    if stage in {'profile', 'vacancy', 'interview', 'match'}:
        if stage != 'match':
            app.findings = []
        app.changes = []
        app.changes_generated = False
        app.section_order = []
        app.order_reason = ''
        app.letter_text = ''
        app.letter_confirmed = False
        app.letter_fact_ids = []
    if stage in {'profile', 'vacancy'}:
        app.questions = []
        app.interview_done = False
    if stage == 'vacancy':
        app.provenance.pop('research', None)
        app.research = None
        app.sources = []
        app.company_site = ''
    if stage in {'profile', 'vacancy', 'interview', 'changes', 'match'}:
        app.resume_text = ''
        app.resume_confirmed = False


def check_inputs(app):
    require(app.profile_confirmed and app.facts, 'Сначала проверьте и подтвердите профиль.')
    require(app.vacancy_confirmed and app.vacancy.requirements, 'Сначала проверьте и подтвердите требования вакансии.')


def conservative_findings(app, findings):
    """Absence of a conflict cannot establish availability or future duties."""
    requirements = {r.id: r for r in app.vacancy.requirements}
    for finding in findings:
        requirement = requirements[finding.requirement_id]
        if requirement.category == 'condition':
            answers = {f'answer-{q.id}' for q in app.questions
                       if q.requirement_id == requirement.id and q.answer and not q.skipped}
            if not (answers & set(finding.fact_ids)):
                finding.status = 'unknown'
                finding.fact_ids = []
                finding.explanation = 'Условие работы не подтверждено вашим ответом. Отсутствие противоречий в резюме не означает согласие с графиком или форматом.'
        elif requirement.category == 'responsibility' and finding.status == 'supported':
            finding.status = 'partial'
            finding.explanation = 'В профиле есть связанный опыт. Это будущая обязанность: конкретный объём задач и формат взаимодействия нужно обсудить с работодателем.'
    return findings


async def extract_profile(app):
    require(bool(app.resume_source.strip()), 'Сначала загрузите PDF или вставьте текст резюме.')
    spans = source_spans(app.resume_source)
    result = await providers.structured(ProfileExtraction,
        'Извлеки только факты кандидата. Для каждого факта evidence является ТОЧНОЙ цитатой исходного текста. '
        'Обязательно укажи source_refs — список ID предоставленных фрагментов T01, T02 и т.д. '
        'Если в PDF слова разбиты пробелами, в text восстанови читаемое написание, '
        'а evidence скопируй из оригинала или оставь пустым, сохранив правильные source_refs. '
        'Контакты тоже извлекай. Не выдумывай факты и не принимай чужие примеры за опыт кандидата.',
        {'source_spans': [{'id': key, 'text': raw} for key, raw in spans.items()]})
    valid, candidates, used, broad = [], [], set(), 0
    for fact in result.facts:
        if not fact.id or fact.id in used:
            fact.id = uid()
        used.add(fact.id)
        evidence = anchor_quote(fact.evidence, app.resume_source)
        refs = [resolve_ref(ref, spans, {k: k for k in spans}, 'T') for ref in fact.source_refs]
        refs = list(dict.fromkeys(ref for ref in refs if ref))
        if not evidence and refs:
            # A real source excerpt is sufficient to review an extraction; it
            # does not certify semantic truth. Keep the broad raw quote visible.
            evidence = '\n\n'.join(spans[ref] for ref in refs)
            if len(evidence) > 4000:
                evidence = ''
            else:
                broad += 1
        if not evidence:
            fact.evidence, fact.source_refs, fact.page = '', [], None
            candidates.append(fact)
            continue
        fact.evidence = evidence
        fact.source_refs = refs
        fact.page = next((i + 1 for i, page in enumerate(app.resume_pages) if anchor_quote(evidence, page)), None)
        fact.origin = 'document'
        valid.append(fact)
    messages = []
    if valid:
        invalidate(app, 'profile')
        app.facts, app.profile_confirmed = valid, False
    if candidates:
        messages.append(f'Для {len(candidates)} предложений не найдено надёжной цитаты. Они показаны отдельно и не используются в анализе. Проверьте их и добавьте нужные как свои факты.')
    if broad:
        messages.append(f'Для {broad} фактов показан исходный фрагмент целиком. Проверьте, что он действительно подтверждает формулировку.')
    if not result.facts:
        messages.append('AI не выделил факты. Исходный текст и прежний профиль сохранены. Можно добавить факт вручную или повторить разбор.')
    app.profile_candidates = candidates
    app.notices = messages
    app.stage_notices['profile'] = messages


async def extract_vacancy(app):
    require(bool(app.vacancy_source.strip()), 'Вставьте текст вакансии или получите страницу по ссылке.')
    result = await providers.structured(Vacancy,
        'Извлеки название позиции, компанию и требования. evidence для каждого требования — ТОЧНАЯ цитата текста. '
        'priority required только для явно обязательного; preferred только для явно желательного; '
        'иначе unspecified. Не превращай обязанности в обязательный прошлый опыт. Не выдумывай требования.',
        {'text': app.vacancy_source})
    validate_vacancy(result, app.vacancy_source)
    invalidate(app, 'vacancy')
    app.vacancy = result
    app.vacancy_confirmed = False


def validate_vacancy(vacancy, source):
    unique_ids(vacancy.requirements)
    require(bool(vacancy.requirements), 'Добавьте хотя бы одно требование с цитатой из вакансии.')
    for r in vacancy.requirements:
        quote = anchor_quote(r.evidence, source)
        require(bool(quote), 'У требования нет точной цитаты в тексте вакансии. Исправьте цитату.')
        r.evidence = quote


async def match(app):
    check_inputs(app)
    fact_aliases, facts = catalog(app.facts, 'F')
    req_aliases, requirements_data = catalog(app.vacancy.requirements, 'R')
    result = await providers.structured(MatchResult,
        'Сопоставь КАЖДОЕ требование с подтверждёнными фактами. supported = прямое подтверждение; '
        'partial = только часть требования; unknown = данных нет; gap = факты явно противоречат требованию. '
        'Отсутствие упоминания не означает отсутствие навыка. Объясняй основания без процентов. '
        'Используй ТОЛЬКО короткие ID из каталогов: requirement_id R01, R02; fact_ids F01, F02. '
        'Для supported/partial/gap обязательны подходящие fact_ids. Не ссылайся на навыки из вакансии как на опыт кандидата.',
        {'facts': facts, 'requirements': requirements_data})
    wanted = {r.id for r in app.vacancy.requirements}
    known = {f.id for f in app.facts}
    grouped = {}
    ignored = 0
    for finding in result.findings:
        requirement_id = resolve_ref(finding.requirement_id, wanted, req_aliases, 'R')
        if not requirement_id:
            ignored += 1
            continue
        finding.requirement_id = requirement_id
        resolved = [resolve_ref(ref, known, fact_aliases, 'F') for ref in finding.fact_ids]
        invalid = any(ref is None for ref in resolved)
        finding.fact_ids = list(dict.fromkeys(ref for ref in resolved if ref))
        finding.needs_review = invalid or (finding.status in {'supported', 'partial', 'gap'} and not finding.fact_ids)
        if finding.needs_review:
            finding.status = 'unknown'
            finding.explanation = 'Модель не смогла связать вывод с подтверждёнными фактами. Это требование требует проверки: выберите факты вручную или уточните опыт в интервью.'
        grouped.setdefault(requirement_id, []).append(finding)
    findings = []
    for requirement in app.vacancy.requirements:
        entries = grouped.get(requirement.id, [])
        if len(entries) == 1:
            findings.append(entries[0])
        else:
            findings.append(Finding(requirement_id=requirement.id, status='unknown', needs_review=True,
                                   explanation='Модель пропустила требование или вернула несколько неоднозначных выводов. Проверьте его вручную либо уточните опыт.'))
    invalidate(app, 'match')
    app.findings = conservative_findings(app, findings)
    reviews = sum(f.needs_review for f in app.findings)
    app.stage_notices['match'] = ([f'Анализ сохранён. Требуют проверки: {reviews}. Остальные выводы доступны; можно продолжить интервью или уточнить отмеченные строки вручную.'] if reviews else [])
    if ignored:
        app.stage_notices['match'].append(f'Исключено ссылок на неизвестные требования: {ignored}.')
    # Keep answered questions; new questions arise only for unresolved requirements.
    existing = {q.requirement_id for q in app.questions}
    requirements = {r.id: r for r in app.vacancy.requirements}
    for finding in app.findings:
        if len(app.questions) >= 4:
            break
        r = requirements[finding.requirement_id]
        if finding.status in {'partial', 'unknown'} and r.id not in existing:
            text = (f'Подходит ли вам условие «{r.text}»? Подтвердите доступность или укажите ограничения.'
                    if r.category == 'condition' else
                    f'Что из вашего опыта подтверждает «{r.text}»? Укажите личную роль и результат; если опыта нет, так и напишите.')
            app.questions.append(Question(requirement_id=r.id, text=text))


def percentage_values(text):
    return {number.replace(',', '.') for number in re.findall(
        r'(\d+(?:[.,]\d+)?)\s*(?:%|процент\w*)', text, re.I)}


async def propose_changes(app):
    check_inputs(app)
    require(bool(app.findings), 'Сначала выполните анализ соответствия.')
    aliases, fact_data = catalog(app.facts, 'F')
    short_ids = {value: key for key, value in aliases.items()}
    findings = [{**f.model_dump(), 'fact_ids': [short_ids[x] for x in f.fact_ids if x in short_ids]} for f in app.findings]
    result = await providers.structured(ChangeResult,
        RESUME_ADAPTATION_PROMPT,
        {'facts': fact_data, 'vacancy': app.vacancy.model_dump(), 'findings': findings})
    facts = {f.id: f for f in app.facts}
    used, used_ids, valid = set(), set(), []
    rejected = 0
    for change in result.changes:
        fact_id = resolve_ref(change.fact_id, facts, aliases, 'F')
        if not fact_id:
            matches = [f.id for f in app.facts if normalized_evidence(f.text, True)[0] == normalized_evidence(change.before, True)[0]]
            fact_id = matches[0] if len(matches) == 1 else None
        fact = facts.get(fact_id)
        support_refs = [resolve_ref(ref, facts, aliases, 'F') for ref in change.supporting_fact_ids]
        support_ids = list(dict.fromkeys(ref for ref in support_refs if ref and ref != fact_id))
        allowed = ({'summary', 'skills', 'experience', 'projects', 'education', 'achievements', 'languages'}
                   if fact and fact.section == 'summary' else
                   {'skills', 'experience', 'projects', 'education', 'achievements'}
                   if fact and fact.section == 'skills' else set())
        invalid_support = (any(ref is None for ref in support_refs) or
                           any(facts[ref].section not in allowed for ref in support_ids))
        basis = '\n'.join(facts[ref].text for ref in [fact_id, *support_ids] if ref in facts)
        # A count such as 20 bugs cannot become an invented 20% improvement.
        if (not fact or invalid_support or fact_id in used or fact.section == 'contacts' or
                normalized_evidence(change.before, True)[0] != normalized_evidence(fact.text, True)[0] or
                not set(re.findall(r'\d+(?:[.,]\d+)?', change.after)) <= set(re.findall(r'\d+(?:[.,]\d+)?', basis)) or
                not percentage_values(change.after) <= percentage_values(basis)):
            rejected += 1
            continue
        change.fact_id = fact_id
        change.supporting_fact_ids = support_ids
        change.before = fact.text
        if change.id in used_ids:
            change.id = uid()
        used_ids.add(change.id)
        change.decision = 'pending'
        used.add(change.fact_id)
        valid.append(change)
    invalidate(app, 'changes')
    app.changes = valid
    app.stage_notices['changes'] = ([f'Пропущено небезопасных или неподтверждённых правок: {rejected}. Остальные предложения доступны. Для пропущенных правок сохранён исходный текст.'] if rejected else [])
    existing_sections = {f.section for f in app.facts}
    app.section_order = (['contacts'] if 'contacts' in existing_sections else []) + [
        section for section in dict.fromkeys(result.section_order) if section in existing_sections and section != 'contacts']
    app.order_reason = result.order_reason
    app.changes_generated = True


async def generate_letter(app, tone, length):
    check_inputs(app)
    aliases, fact_data = catalog(app.facts, 'F')
    short_ids = {value: key for key, value in aliases.items()}
    result = await providers.structured(LetterResult,
        'Напиши персональное сопроводительное письмо. Используй только подтверждённые факты; '
        'не выдумывай личную мотивацию и симпатию к компании. Укажи fact_ids всех использованных фактов только из каталога F01, F02. '
        'Не обещай соответствие требованиям, где нет подтверждения. Без канцелярита и общих похвал. '
        'Требования и условия вакансии НЕ являются фактами кандидата. Не обещай часы работы, удалённый формат, '
        'переезд или выполнение тестового задания без прямого подтверждения кандидата. '
        'Не усиливай уровень владения: знание Python не означает уверенное или экспертное владение. '
        'Не добавляй числовые данные из вакансии или исследования в описание кандидата. '
        'professional = деловой тон; warm = дружелюбный; concise = прямой. short до 130 слов; standard до 250 слов.',
        {'facts': fact_data, 'vacancy': app.vacancy.model_dump(),
         'findings': [{**f.model_dump(), 'fact_ids': [short_ids[x] for x in f.fact_ids if x in short_ids]} for f in app.findings],
         'research': app.research.model_dump() if app.research else None, 'tone': tone, 'length': length})
    resolved = [resolve_ref(ref, {f.id for f in app.facts}, aliases, 'F') for ref in result.fact_ids]
    app.stage_notices['letter'] = (['В письме есть непроверенные ссылки на факты. Сохранён только черновик: сверьте каждое утверждение с профилем и исправьте текст перед подтверждением.']
                                   if not resolved or any(ref is None for ref in resolved) else [])
    confirmed_text = '\n'.join(f.text for f in app.facts)
    require(set(re.findall(r'\d+(?:[.,]\d+)?', result.text)) <= set(re.findall(r'\d+(?:[.,]\d+)?', confirmed_text)),
            'В письме появились неподтверждённые числовые сведения. Повторите генерацию или напишите текст вручную.')
    require(not re.search(r'тестов\w*\s+задан', result.text, re.I) or bool(re.search(r'тестов\w*\s+задан', confirmed_text, re.I)),
            'AI предложил неподтверждённое тестовое задание. Повторите генерацию или отредактируйте письмо вручную.')
    app.letter_text = result.text
    app.letter_fact_ids = list(dict.fromkeys(ref for ref in resolved if ref))
    app.letter_tone, app.letter_length = tone, length
    app.letter_confirmed = False


def stages(app):
    return {
        'profile': 'done' if app.profile_confirmed else 'review' if app.facts or app.profile_candidates else 'empty',
        'vacancy': 'done' if app.vacancy_confirmed else 'review' if app.vacancy.requirements else 'empty',
        'match': ('review' if any(f.needs_review for f in app.findings) else 'done') if app.findings else 'empty',
        'interview': 'done' if app.interview_done else 'review' if app.questions else 'empty',
        'changes': ('review' if any(c.decision == 'pending' for c in app.changes) else 'done') if app.changes_generated else 'empty',
        'resume': 'done' if app.resume_confirmed else 'review' if app.resume_text else 'empty',
        'letter': 'done' if app.letter_confirmed else 'review' if app.letter_text else 'empty',
        'research': ('review' if app.research.summary_pending or not app.research.claims else 'done') if app.research else 'empty',
        'package': 'done' if app.resume_confirmed and app.letter_confirmed else 'empty',
    }
