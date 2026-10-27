import { homeView, stageView, factsRow, requirementRow, STEPS, esc, privacyView } from './views.js';

const $ = selector => document.querySelector(selector);
const state = { app: null, apps: [], step: 'profile', busy: false, dirty: false, error: '', routeHash: location.hash };
let toastTimer, routeSequence = 0;
const base = () => `/api/applications/${state.app.id}`;

function toast(message) {
  $('#notification').textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#notification').textContent = ''; }, 4500);
}

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json', ...options.headers } });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const error = new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? 'Проверьте заполнение полей и ограничения длины текста.' : 'Не удалось выполнить запрос. Проверьте соединение с локальным сервером.');
    error.status = response.status;
    throw error;
  }
  return payload;
}

function dialog(title, body, confirm = 'Продолжить', cancel = 'Отмена') {
  const element = $('#dialog');
  $('#dialog-body').innerHTML = `<h2>${esc(title)}</h2>${body}`;
  $('#dialog-confirm').textContent = confirm;
  $('#dialog-cancel').textContent = cancel;
  element.showModal();
  return new Promise(resolve => {
    const finish = value => { element.close(); resolve(value); };
    $('#dialog-confirm').onclick = () => finish(true);
    $('#dialog-cancel').onclick = () => finish(false);
    element.oncancel = event => { event.preventDefault(); finish(false); };
  });
}

function navigation() {
  const app = state.app;
  $('#crumb').textContent = app ? `/ ${app.vacancy.title || 'Новый отклик'}` : '';
  if (!app) {
    $('#navigation').innerHTML = `<div class="nav-label">Рабочее пространство</div><a class="nav-item current" href="#" aria-current="page"><span class="nav-number">↗</span>Мои отклики</a><button class="nav-item" data-action="new"><span class="nav-number">+</span>Новый отклик</button><button class="nav-item" data-action="demo"><span class="nav-number">▷</span>Попробовать демо</button>`;
  } else {
    $('#navigation').innerHTML = `<a class="nav-item nav-back" href="#">← Все отклики</a><div class="nav-label">Подготовка отклика</div>${STEPS.map(([key, title], index) => {
      const status = app.stages[key];
      return `<a class="nav-item ${key === state.step ? 'current' : ''}" href="#app/${app.id}/${key}" ${key === state.step ? 'aria-current="step"' : ''}><span class="nav-number status-${status}">${status === 'done' ? '✓' : String(index + 1).padStart(2, '0')}</span>${title}</a>`;
    }).join('')}`;
  }
}

function render() {
  state.routeHash = location.hash;
  navigation();
  $('#content').innerHTML = state.app ? stageView(state.app, state.step) : homeView(state.apps);
  state.dirty = false;
  displayError();
  document.title = state.app ? `${STEPS.find(s => s[0] === state.step)?.[1]} · Sber21` : 'Мои отклики · Sber21';
  const current = $('#navigation [aria-current="step"]');
  if (current && window.innerWidth <= 760) current.scrollIntoView({ block: 'nearest', inline: 'center' });
}

function displayError() {
  $('#error-box')?.remove();
  if (!state.error) return;
  const node = document.createElement('div');
  node.id = 'error-box'; node.className = 'error-box'; node.setAttribute('role', 'alert');
  node.innerHTML = `${esc(state.error)}<p>Введённый текст остался на экране. Можно исправить данные вручную или повторить действие.</p>${state.errorStatus === 409 ? '<button class="text-button" data-action="refresh-data">Обновить данные</button>' : ''} <button class="text-button" data-action="dismiss-error">Закрыть</button>`;
  $('#content').prepend(node);
}

function setBusy(value, label = '') {
  state.busy = value;
  $('#operation-status')?.remove();
  $('#content').setAttribute('aria-busy', String(value));
  document.querySelectorAll('button,input,textarea,select').forEach(node => {
    if (node.closest('dialog')) return;
    if (value) { node.dataset.wasDisabled = String(node.disabled); node.disabled = true; }
    else { node.disabled = node.dataset.wasDisabled === 'true'; delete node.dataset.wasDisabled; }
  });
  if (value) {
    const node = document.createElement('div'); node.id = 'operation-status'; node.className = 'operation-status';
    node.setAttribute('role', 'status'); node.innerHTML = `<span class="skeleton" aria-hidden="true"></span><span>${esc(label || 'Сохраняем изменения…')}</span>`;
    $('#content').prepend(node);
  }
}

async function perform(callback, label = '', success = 'Изменения сохранены') {
  if (state.busy) return;
  const startingVersion = state.app?.version;
  state.error = ''; state.errorStatus = null; displayError(); setBusy(true, label);
  try {
    const result = await callback();
    if (result?.id) state.app = result;
    state.dirty = false;
    setBusy(false); render();
    if (success) toast(success);
    return result;
  } catch (error) {
    setBusy(false);
    state.errorStatus = error.status;
    state.error = error instanceof TypeError ? 'Не удалось связаться с сервером. Введённый текст остался на экране. Проверьте, что приложение запущено.' : error.message;
    if (state.app?.version !== startingVersion) render();
    displayError(); $('#error-box')?.scrollIntoView({ block: 'nearest' });
    return null;
  }
}

const update = (path, body = {}, method = 'POST') => request(`${base()}/${path}`, { method, body: JSON.stringify({ version: state.app.version, ...body }) });

async function aiConfirm(label, search = false) {
  return dialog(label, `<p>${search ? 'Название компании или ссылка будут отправлены в Tavily. Текст источников' : 'Текст резюме, вакансии или подтверждённые факты текущего этапа'} будет передан ${search ? 'провайдерам поиска и AI' : 'в GigaChat'} для обработки. Запрос использует ваш API и может расходовать его лимит.</p><p>В демопримере эта кнопка запускает настоящую обработку синтетических данных.</p>`, 'Запустить обработку');
}

async function go(step) { location.hash = `app/${state.app.id}/${step}`; }

async function loadRoute() {
  const seq = ++routeSequence;
  const parts = location.hash.slice(1).split('/');
  state.error = '';
  $('#content').innerHTML = '<div class="skeleton-page" role="status" aria-label="Открываем отклик"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
  try {
    if (parts[0] === 'app' && parts[1]) {
      const app = await request(`/api/applications/${encodeURIComponent(parts[1])}`);
      if (seq !== routeSequence) return;
      state.app = app;
      state.step = STEPS.some(s => s[0] === parts[2]) ? parts[2] : 'profile';
    } else if (parts[0] === 'privacy') {
      state.app = null; navigation(); $('#content').innerHTML = privacyView(); return;
    } else {
      const apps = await request('/api/applications');
      if (seq !== routeSequence) return;
      state.app = null; state.apps = apps;
    }
    render();
    window.scrollTo({ top: 0, behavior: 'instant' });
    $('#content').focus({ preventScroll: true });
  } catch (error) {
    state.app = null; navigation();
    $('#content').innerHTML = `<div class="empty"><h1>Не удалось открыть страницу</h1><p>${esc(error.message)}</p><a class="button" href="#">К моим откликам</a><button class="button secondary" data-action="reload">Повторить</button></div>`;
  }
}

async function create(demo) {
  ++routeSequence;
  await perform(async () => {
    const app = await request('/api/applications', { method: 'POST', body: JSON.stringify({ demo }) });
    state.app = app; state.step = demo ? 'match' : 'profile';
    history.pushState(null, '', `#app/${app.id}/${state.step}`);
    return app;
  }, demo ? 'Готовим синтетический пример…' : 'Создаём отклик…', demo ? 'Открыт демонстрационный пример. AI не вызывался.' : 'Отклик создан');
}

function collectFacts() {
  return [...document.querySelectorAll('.fact-row')].map(row => ({
    id: row.dataset.id, section: row.querySelector('[data-field="section"]').value,
    text: row.querySelector('[data-field="text"]').value.trim()
  }));
}

function collectVacancy() {
  return {
    title: $('#vacancy-title').value.trim(), company: $('#vacancy-company').value.trim(),
    requirements: [...document.querySelectorAll('.requirement-row')].map(row => ({
      id: row.dataset.id, text: row.querySelector('[data-field="text"]').value.trim(),
      evidence: row.querySelector('[data-field="evidence"]').value.trim(),
      priority: row.querySelector('[data-field="priority"]').value,
      category: row.querySelector('[data-field="category"]').value
    }))
  };
}

async function versionHistory() {
  const items = await request(`${base()}/versions`);
  const markup = `<p>Восстановление создаст новую версию. Текущие результаты останутся в истории.</p><div class="versions-list">${items.map(v => `<label class="version-row"><span>Версия ${v.version} · ${new Date(v.updated_at).toLocaleString('ru')}</span><input type="radio" name="version" value="${v.version}" ${v.version === state.app.version ? 'checked' : ''}></label>`).join('')}</div>`;
  if (await dialog('История отклика', markup, 'Восстановить')) {
    const target = Number($('#dialog-body input:checked')?.value);
    if (target && target !== state.app.version) await perform(() => update('restore', { target_version: target }), '', 'Версия восстановлена');
  }
}

async function researchAction(reuse) {
  const website = $('#company-site').value.trim();
  if (!(await dialog(reuse ? 'Подготовить отчёт из источников?' : 'Найти материалы и подготовить отчёт?',
    `<p>${reuse ? 'Используем сохранённые источники и одну AI-задачу. Новый поиск не выполняется.' : 'До 6 поисковых запросов Tavily, 2 запросов чтения страниц и 2 AI-задач: план поиска и отчёт. Это расходует лимиты ваших API.'}</p><p>Передаются название компании, сайт, название роли и публичные материалы.</p>`, 'Запустить'))) return;
  const result = await perform(()=>update('research', {website, reuse_sources:reuse}), reuse ? 'Готовим отчёт из сохранённых источников…' : 'Определяем компанию, ищем материалы и готовим отчёт…', '');
  if (result) toast(result.research?.claims.length ? 'Отчёт готов к проверке' : 'Отчёт пока не готов. На экране указана причина и следующий шаг.');
}

const actions = {
  new: () => create(false), demo: () => create(true),
  reload: () => loadRoute(),
  'refresh-data': async () => {
    if (state.dirty && !(await dialog('Обновить данные?', '<p>Несохранённый текст на этом экране будет заменён сохранённой версией.</p>', 'Обновить'))) return;
    await loadRoute();
  },
  'adopt-fact': node => {
    const fact = state.app.profile_candidates.find(f => f.id === node.dataset.candidate);
    if (!fact || [...document.querySelectorAll('.fact-row')].some(row=>row.dataset.id===fact.id)) return;
    $('#facts').insertAdjacentHTML('beforeend', factsRow({...fact, evidence:'', origin:'user'}));
    node.disabled = true; node.textContent = 'Добавлено для подтверждения'; state.dirty = true;
    toast('Проверьте текст и подтвердите профиль, чтобы использовать факт дальше');
  },
  'save-match-review': node => {
    const form = node.closest('.match-review');
    const finding = {requirement_id:form.dataset.requirement, status:form.querySelector('[data-review="status"]').value,
      fact_ids:[...form.querySelector('[data-review="facts"]').selectedOptions].map(o=>o.value),
      explanation:form.querySelector('[data-review="explanation"]').value.trim()};
    return perform(()=>update('match-review',{finding},'PUT'), '', 'Вывод сохранён. Зависимые документы нужно собрать заново.');
  },
  privacy: () => { location.hash = 'privacy'; },
  'dismiss-error': () => { state.error = ''; displayError(); },
  'add-fact': () => { $('#facts').insertAdjacentHTML('beforeend', factsRow({ id: crypto.randomUUID(), section: 'projects', text: '', origin: 'user' })); state.dirty = true; $('#facts .fact-row:last-child textarea').focus(); },
  'remove-fact': node => { node.closest('.fact-row').remove(); state.dirty = true; },
  'save-profile': async () => {
    const facts = collectFacts();
    if (!facts.length || facts.some(f => !f.text)) { state.error = 'Заполните текст каждого факта или удалите пустые строки.'; displayError(); return; }
    const result = await perform(() => update('profile', { facts }, 'PUT'), '', 'Профиль подтверждён');
    if (result) go('vacancy');
  },
  'save-resume-source': () => {
    const text = $('#resume-source').value.trim();
    return perform(() => update('resume-source', { text }, 'PUT'), '', 'Текст сохранён. Теперь можно разобрать его через AI или заполнить профиль вручную.');
  },
  'extract-profile': async () => {
    if (!(await aiConfirm('Разобрать резюме через AI?'))) return;
    const text = $('#resume-source').value.trim();
    await perform(async () => {
      if (text !== state.app.resume_source) state.app = await update('resume-source', { text }, 'PUT');
      return update('profile-extract');
    }, 'Выделяем факты из резюме…', 'Проверьте извлечённые факты и подтвердите профиль');
  },
  'add-requirement': () => { $('#requirements').insertAdjacentHTML('beforeend', requirementRow({ id: crypto.randomUUID(), text: '', evidence: '', priority: 'unspecified', category: 'skill' })); state.dirty = true; $('#requirements .requirement-row:last-child textarea').focus(); },
  'remove-requirement': node => { node.closest('.requirement-row').remove(); state.dirty = true; },
  'save-vacancy-source': () => perform(() => update('vacancy-source', { text: $('#vacancy-source').value.trim() }, 'PUT'), '', 'Текст вакансии сохранён'),
  'fetch-vacancy': async () => {
    if (!(await aiConfirm('Получить страницу вакансии?', true))) return;
    await perform(() => update('vacancy-fetch', { url: $('#vacancy-url').value.trim() }), 'Получаем текст вакансии…', 'Проверьте полученный текст и выделите требования');
  },
  'extract-vacancy': async () => {
    if (!(await aiConfirm('Выделить требования через AI?'))) return;
    const text = $('#vacancy-source').value.trim();
    await perform(async () => {
      if (text !== state.app.vacancy_source) state.app = await update('vacancy-source', { text }, 'PUT');
      return update('vacancy-extract');
    }, 'Выделяем требования вакансии…', 'Проверьте обязательность требований и цитаты');
  },
  'save-vacancy': async () => {
    const vacancy = collectVacancy();
    const result = await perform(() => update('vacancy', { vacancy }, 'PUT'), '', 'Вакансия подтверждена');
    if (result) go('match');
  },
  match: async () => {
    if (!(await dialog('Сравнить профиль с вакансией заново?', '<p>AI заново проверит каждое требование по подтверждённым фактам профиля.</p><p>Текущая таблица, включая ручные выводы, будет заменена. Адаптацию, итоговое резюме и письмо потребуется подготовить заново; прежние версии останутся в истории. Запрос расходует лимит GigaChat.</p>', 'Сравнить заново'))) return;
    await perform(() => update('match'), 'Сопоставляем требования с подтверждёнными фактами…', 'Анализ сохранён');
  },
  'save-interview': async () => {
    const answers = Object.fromEntries([...document.querySelectorAll('[data-question]')].map(n => [n.dataset.question, n.value]));
    const result = await perform(() => update('interview', { answers }, 'PUT'), '', 'Ответы подтверждены. Обновите анализ с учётом новых фактов.');
    if (result) go(state.app.findings.length ? 'changes' : 'match');
  },
  'skip-interview': async () => {
    const result = await perform(() => update('interview', { skip: true }, 'PUT'), '', 'Интервью пропущено. Неподтверждённые сведения не добавлены.');
    if (result) go('changes');
  },
  changes: async () => {
    if (!(await aiConfirm('Предложить адаптацию резюме?'))) return;
    await perform(() => update('changes'), 'Готовим правки с объяснением…', 'Проверьте каждую предложенную правку');
  },
  'save-decisions': async () => {
    const decisions = Object.fromEntries([...document.querySelectorAll('[data-decision]')].map(n => [n.dataset.decision, n.value]));
    const result = await perform(() => update('changes', { decisions, use_suggested_order: $('#use-order')?.checked ?? true }, 'PUT'), '', 'Решения сохранены');
    if (result && !result.changes.some(c => c.decision === 'pending')) go('resume');
  },
  'assemble-resume': () => perform(() => update('resume-assemble'), '', 'Черновик собран из подтверждённых фактов и принятых правок'),
  'save-resume': node => perform(() => update('resume', { text: $('#resume-editor').value, confirmed: node.dataset.confirm === 'true' }, 'PUT'), '', node.dataset.confirm === 'true' ? 'Резюме подтверждено. Экспорт доступен.' : 'Черновик резюме сохранён'),
  'generate-letter': async () => {
    if (!(await aiConfirm('Подготовить сопроводительное письмо?'))) return;
    const tone = $('#letter-tone').value, length = $('#letter-length').value;
    await perform(() => update('letter-generate', { tone, length }), 'Готовим письмо по вашим фактам…', 'Письмо готово к проверке');
  },
  'save-letter': node => perform(() => update('letter', { text: $('#letter-editor').value, confirmed: node.dataset.confirm === 'true' }, 'PUT'), '', node.dataset.confirm === 'true' ? 'Письмо подтверждено' : 'Черновик письма сохранён'),
  research: () => researchAction(false),
  'research-summary': () => researchAction(true),
  'copy-resume': async () => { await navigator.clipboard.writeText(state.app.resume_text); toast('Текст резюме скопирован'); },
  'copy-letter': async () => { await navigator.clipboard.writeText(state.app.letter_text); toast('Письмо скопировано'); },
  versions: () => versionHistory(),
  delete: async () => {
    if (!(await dialog('Удалить отклик с этого компьютера?', '<p>Будут удалены профиль этого отклика, документы и вся история его версий. Это действие нельзя отменить.</p>', 'Удалить отклик'))) return;
    await perform(async () => { await request(base(), { method: 'DELETE' }); state.app = null; state.apps = await request('/api/applications'); history.pushState(null, '', '#'); }, '', 'Отклик удалён');
  },
};

document.addEventListener('click', async event => {
  const exportLink = event.target.closest('a[href*="/export/"]');
  if (exportLink && state.dirty && state.step !== 'research') {
    event.preventDefault(); toast('Сначала сохраните и подтвердите изменения документа'); return;
  }
  const sourceLink = event.target.closest('[data-source-link]');
  if (sourceLink) { event.preventDefault(); document.getElementById('source-' + sourceLink.dataset.sourceLink)?.scrollIntoView({block:'center'}); return; }
  const link = event.target.closest('a[href^="#"]');
  if (link) {
    if (link.classList.contains('skip')) { event.preventDefault(); $('#content').focus(); return; }
    if (state.busy) { event.preventDefault(); toast('Дождитесь завершения текущей операции'); return; }
    if (state.dirty) {
      event.preventDefault();
      if (await dialog('Есть несохранённые изменения', '<p>Сохраните данные на текущем этапе, чтобы использовать их дальше. Перейти без сохранения?</p>', 'Перейти без сохранения', 'Остаться')) { state.dirty = false; location.hash = link.getAttribute('href'); }
    }
    return;
  }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled || state.busy || !actions[button.dataset.action]) return;
  if (state.dirty && ['new', 'demo', 'privacy', 'versions', 'copy-letter', 'copy-resume'].includes(button.dataset.action)) {
    toast('Сначала сохраните изменения текущего этапа'); return;
  }
  try { await actions[button.dataset.action](button); }
  catch (error) { state.error = error.message; displayError(); }
});

document.addEventListener('input', event => {
  if (event.target.id === 'app-search') {
    const q = event.target.value.toLocaleLowerCase();
    document.querySelectorAll('.application-row').forEach(row => { row.hidden = !row.dataset.search.includes(q); });
    const count = [...document.querySelectorAll('.application-row')].filter(row => !row.hidden).length;
    $('#search-empty').hidden = count > 0;
  } else if (event.target.closest('#content') && !event.target.closest('dialog')) {
    state.dirty = true;
    if (['resume-editor', 'letter-editor'].includes(event.target.id)) {
      document.querySelectorAll('a[href*="/export/"]').forEach(a => a.setAttribute('aria-disabled', 'true'));
    }
  }
});
document.addEventListener('change', async event => {
  if (event.target.id !== 'resume-file') return;
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024 || !file.name.toLowerCase().endsWith('.pdf')) { state.error = 'Выберите PDF размером до 10 МБ.'; displayError(); return; }
  const data = new FormData(); data.append('file', file);
  await perform(() => request(`${base()}/resume-upload?version=${state.app.version}`, { method: 'POST', body: data }), 'Извлекаем текст PDF на этом компьютере…', 'Текст извлечён. Проверьте его перед AI-обработкой.');
});
window.addEventListener('beforeunload', event => { if (state.dirty || state.busy) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('hashchange', async () => {
  if (state.busy) {
    history.replaceState(null, '', state.routeHash || '#');
    toast('Дождитесь завершения текущей операции');
    return;
  }
  if (state.dirty) {
    const target = location.hash;
    history.replaceState(null, '', state.routeHash || '#');
    if (!(await dialog('Есть несохранённые изменения', '<p>Перейти без сохранения введённого текста?</p>', 'Перейти без сохранения', 'Остаться'))) return;
    state.dirty = false;
    history.pushState(null, '', target);
  }
  loadRoute();
});
loadRoute();
