"""Deterministic employer identity filtering and readable evidence selection."""
import html
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

BOILERPLATE = re.compile(r'cookie|куки|персональн\w* данн|рекламн\w* рассыл|подписатьс|подпишитесь|политик\w* конфиденциальн|все права защищены|javascript|войти в аккаунт|accept all|privacy policy', re.I)
TOPIC_WORDS = re.compile(r'компани|постав|достав|продукт|клиент|разраб|технолог|команд|сотрудник|работодател|собеседован|ваканс|основан|бизнес|производ|сервис|интеграц|логист|рынок|интервью|выручк|офис|склад', re.I)


def identity_key(value):
    return re.sub(r'[^\w]', '', value.casefold().replace('ё', 'е')).replace('_', '')


def same_employer(company, source, website=''):
    """Reject generic search hits before reading pages or using their evidence."""
    host = (urlsplit(source.get('url', '')).hostname or '').lower()
    domain = (urlsplit(website).hostname or '').lower()
    if domain and (host == domain or host.endswith('.' + domain)):
        return True
    key = identity_key(company)
    title = source.get('title', '')
    snippet = source.get('search_snippet', source.get('snippet', ''))
    if not key or len(key) < 3:
        return False
    # A literal phrase in an article about drinks does not identify an employer.
    if host.removeprefix('www.') in {'tiktok.com','instagram.com','pinterest.com','facebook.com'}:
        return False
    title_key = identity_key(title)
    business = re.compile(r'компани|работодател|ваканси|официальн|работа в|отзывы|доставк|поставщик|employer|careers|company|business', re.I)
    if key in title_key:
        if title_key == key or business.search(title) or re.search(r'\bв\s+[«"]?' + re.escape(company), title, re.I):
            return True
        # Local directories commonly title employer cards with company + city.
        if re.search(re.escape(company) + r'[,·]\s*[А-ЯA-Z]', title):
            return True
    # Reject general job listing pages that mix adjacent employers' descriptions.
    if key not in title_key and re.search(r'ваканси|работа|менеджер|разработчик|оператор', title, re.I):
        return False
    compact = identity_key(snippet)
    start = compact.find(key)
    if start >= 0:
        before = compact[max(0,start-20):start]
        after = compact[start+len(key):start+len(key)+100]
        if re.search(r'(компания|компании|компанию|ооо|работодатель|company)$', before):
            return True
        if re.match(r'(компания|company|поставляет|доставляет|занимается|работодатель)', after):
            return True
        if not title and start < 20 and business.search(snippet):
            return True
    return bool(domain and domain in snippet.lower())


def clean_content(raw):
    text = str(raw or '')[:100000]
    if re.search(r'<(?:html|body|article|main|div|p)[\s>]', text, re.I):
        soup = BeautifulSoup(text, 'html.parser')
        for node in soup.select('script,style,nav,header,footer,form,aside,noscript'):
            node.decompose()
        text = (soup.find('main') or soup.find('article') or soup).get_text('\n', strip=True)
    text = html.unescape(text)
    text = re.sub(r'!\[[^\]]*\]\([^\n)]*\)', '', text)
    lines, seen = [], set()
    for line in text.splitlines():
        original = line.strip()
        if not original or BOILERPLATE.search(original):
            continue
        # Large navigation blocks are not article paragraphs.
        if len(re.findall(r'\]\(', original)) >= 3:
            continue
        line = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', original)
        line = re.sub(r'https?://\S+', '', line)
        line = re.sub(r'^[#>*\s\-]+', '', line).strip(' |')
        line = re.sub(r'\s+', ' ', line)
        if len(line) < 45 or len(re.findall(r'[\w]+', line)) < 6:
            continue
        # Split long flat extraction output without throwing away its tail.
        pieces = re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])', line) if len(line) > 900 else [line]
        for piece in pieces:
            for i in range(0, len(piece), 800):
                part = piece[i:i+800].strip()
                normalized = identity_key(part)
                if len(part) >= 40 and normalized not in seen:
                    lines.append(part)
                    seen.add(normalized)
    return '\n\n'.join(lines)


def select_evidence(raw, company, limit=2800):
    cleaned = clean_content(raw)
    paragraphs = cleaned.split('\n\n') if cleaned else []
    key = identity_key(company)
    scores = [(i, (8 if key and key in identity_key(p) else 0) + min(5, len(TOPIC_WORDS.findall(p)))) for i, p in enumerate(paragraphs)]
    # Never allocate the entire context window to a menu or the first paragraph.
    selected, size = [], 0
    for i, score in sorted(scores, key=lambda item: (-item[1], item[0])):
        p = paragraphs[i]
        if size + len(p) + 2 > limit:
            continue
        if score or not selected:
            selected.append((i,p)); size += len(p)+2
        if len(selected) >= 5:
            break
    return '\n\n'.join(p for _,p in sorted(selected))


def evidence_catalog(sources, company):
    """Stable server-assigned excerpts eliminate model copying of long quotations."""
    context, lookup = [], {}
    for source in sources:
        text = select_evidence(source.get('snippet', ''), company, 1800)
        excerpts = []
        for index, paragraph in enumerate(text.split('\n\n') if text else [], 1):
            ref = f"{source['id']}-E{index}"
            lookup[ref] = {'source_id':source['id'], 'text':paragraph}
            excerpts.append({'id':ref,'text':paragraph})
        if excerpts:
            context.append({'id':source['id'], 'title':source.get('title',''), 'url':source.get('url',''),
                            'published_date':source.get('published_date',''), 'excerpts':excerpts})
    return context, lookup


def source_kind(source):
    url = source.get('url', '')
    title = source.get('title', '')
    if source.get('source_type') == 'official_site':
        return 'official'
    if re.search(r'/vacancy/|/vacancies/|/job/', url):
        return 'vacancy'
    if re.search(r'отзывы|review', title, re.I):
        return 'reviews'
    return 'public'
