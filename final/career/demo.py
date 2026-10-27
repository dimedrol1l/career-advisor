"""Synthetic, visibly labelled examples. Never used as a live provider fallback."""
from pathlib import Path
from career.schemas import Application, uid

DEMO_DIR = Path(__file__).resolve().parents[1] / 'demo'


def example():
    app = Application.model_validate_json((DEMO_DIR / 'example.json').read_text())
    app.id, app.version, app.created_at, app.updated_at = uid(), 0, '', ''
    app.provenance = {stage: 'example' for stage in ['profile', 'vacancy', 'match', 'interview', 'changes', 'resume', 'letter', 'research']}
    return app
