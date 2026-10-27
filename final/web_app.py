"""Local FastAPI entry point: static browser UI and the versioned career API."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from career.api import router as career_router
from career.storage import interrupt_runs

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'web' / 'static'


@asynccontextmanager
async def lifespan(_: FastAPI):
    interrupt_runs()
    yield


app = FastAPI(title='Sber21 Career Workspace', version='0.3.0', lifespan=lifespan)
app.include_router(career_router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
app.mount('/demo', StaticFiles(directory=BASE_DIR / 'demo'), name='demo')


@app.middleware('http')
async def security_headers(request, call_next):
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('origin')
        expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
        if (origin and origin != expected) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Межсайтовые запросы запрещены.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@app.get('/', response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / 'index.html')


@app.get('/api/health')
async def health() -> dict:
    # Local configuration presence only. Never authenticate or call a provider.
    return {'status': 'ok',
            'gigachat_configured': bool(os.getenv('GIGACHAT_CREDENTIALS', '').strip()),
            'tavily_configured': bool(os.getenv('TAVILY_API_KEY', '').strip())}
