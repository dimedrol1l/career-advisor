"""Source anchoring and short model references; never fuzzy semantic matching."""
import re


def normalized_evidence(text, compact_letters=False):
    normalized, positions = [], []
    translation = str.maketrans({'–': '-', '—': '-', '‑': '-', '−': '-', '«': '"', '»': '"', '“': '"', '”': '"'})
    following = [None] * len(text)
    right = None
    for i in range(len(text) - 1, -1, -1):
        following[i] = right
        if not text[i].isspace():
            right = text[i]
    left = None
    for index, char in enumerate(text):
        if char in {'\u00ad', '\u200b', '\ufeff'}:
            continue
        mapped = ' ' if char.isspace() else char.translate(translation)
        if compact_letters and mapped == ' ':
            right = following[index]
            # Do not merge digits: "2 0" must not establish a quoted "20".
            if left and right and left.isalpha() and right.isalpha():
                continue
        if mapped == ' ' and normalized and normalized[-1] == ' ':
            continue
        if not char.isspace():
            left = char
        normalized.append(mapped)
        positions.append(index)
    return ''.join(normalized), positions


def anchor_quote(quote, source):
    if not quote.strip():
        return ''
    if quote in source:
        return quote
    for compact in (False, True):
        haystack, positions = normalized_evidence(source, compact)
        needle, _ = normalized_evidence(quote.strip(), compact)
        start = haystack.find(needle)
        if needle and start >= 0:
            return source[positions[start]:positions[start + len(needle) - 1] + 1]
    return ''


def source_spans(text):
    """Bounded raw excerpts make provenance possible even without an AI quote."""
    spans, current = [], ''
    for line in text.splitlines(keepends=True):
        for start in range(0, len(line), 800):
            piece = line[start:start + 800]
            if len(current) + len(piece) > 800:
                spans.append(current)
                current = ''
            current += piece
    if current:
        spans.append(current)
    return {f'T{i:02d}': raw for i, raw in enumerate(spans, 1) if raw.strip()}


def catalog(items, prefix):
    aliases, data = {}, []
    for index, item in enumerate(items, 1):
        alias = f'{prefix}{index:02d}'
        aliases[alias] = item.id
        row = item.model_dump()
        row['id'] = alias
        # User-facing provenance fields are not model instructions or task IDs.
        for key in ('origin', 'source_refs', 'page', 'evidence'):
            row.pop(key, None)
        data.append(row)
    return aliases, data


def resolve_ref(value, known, aliases, prefix):
    if value in known:
        return value
    cleaned = str(value).strip().strip('[](){}\"\'` ').upper()
    cleaned = re.sub(r'^(?:FACT(?:_ID)?|REQUIREMENT(?:_ID)?|ФАКТ|ТРЕБОВАНИЕ)\s*[:#]\s*', '', cleaned)
    match = re.fullmatch(re.escape(prefix) + r'0*(\d+)', cleaned)
    if match:
        return aliases.get(f'{prefix}{int(match[1]):02d}')
    return None
