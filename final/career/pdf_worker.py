"""Isolated PDF parser: bytes in, bounded JSON out; no application imports."""
import io
import json
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    # Respect the host's hard limit (macOS may reject raising RLIMIT_DATA).
    if sys.platform == 'linux':
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = min(512 * 1024 * 1024, hard) if hard != resource.RLIM_INFINITY else 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    from pypdf import PdfReader
    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or not data.startswith(b'%PDF-'):
        raise ValueError('invalid')
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError('encrypted')
    if not 1 <= len(reader.pages) <= 20:
        raise ValueError('pages')
    pages = []
    for page in reader.pages:
        # Bound decoded content before extracting glyphs. The Linux worker also
        # has a hard address-space limit; on macOS timeout/CPU remain enforced.
        contents = page.get_contents()
        if contents and len(contents.get_data()) > 8 * 1024 * 1024:
            raise ValueError('text_limit')
        text = page.extract_text() or ''
        pages.append(text)
        if sum(map(len, pages)) > 60000:
            raise ValueError('text_limit')
    if len(''.join(pages).strip()) < 30:
        raise ValueError('scan')
    print(json.dumps({'pages': pages}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else 'invalid'
        print(json.dumps({'error': code if code in {'invalid', 'encrypted', 'pages', 'text_limit', 'scan'} else 'invalid'}))
        sys.exit(1)
