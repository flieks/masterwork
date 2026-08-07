"""Secret redaction of file-derived content before it enters LLM prompts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.api.v1.assets.diagram_service import _diagram_prompt
from app.api.v1.chat.service import _project_context
from app.api.v1.simulations.service import build_prompt
from app.db.models.project import Project
from app.providers.base import Provider, ScannedAsset
from app.services.redact import redact

AWS_KEY = "AKIAIOSFODNN7SECRET1"
GITHUB_TOKEN = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
SK_KEY = "sk-ant-api03-AbCdEf123456GhIjKl"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123DEF456ghi789"
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBg\nkqhkiG9w0BAQ==\n-----END RSA PRIVATE KEY-----"
)


# --- true positives ---------------------------------------------------------


def test_aws_access_key() -> None:
    assert redact(f"uses {AWS_KEY} for S3") == "uses [REDACTED:aws-access-key] for S3"


def test_github_tokens() -> None:
    assert redact(f"push with {GITHUB_TOKEN}") == "push with [REDACTED:github-token]"
    assert "[REDACTED:github-token]" in redact("gho_" + "a1B2" * 9)
    assert "[REDACTED:github-token]" in redact("github_pat_" + "a1B2c3D4e5F6g7H8i9J0k1")


def test_sk_prefixed_key() -> None:
    assert redact(f"key: {SK_KEY}") == "key: [REDACTED:sk-key]"


def test_stripe_key() -> None:
    assert "[REDACTED:stripe-key]" in redact("sk_live_a1B2c3D4e5F6g7H8")


def test_jwt() -> None:
    assert redact(f"Authorization: Bearer {JWT}") == "Authorization: Bearer [REDACTED:jwt]"


def test_pem_private_key_block() -> None:
    out = redact(f"config:\n{PEM}\ndone")
    assert out == "config:\n[REDACTED:private-key]\ndone"


def test_pem_variants() -> None:
    for label in ("", "EC ", "OPENSSH ", "ENCRYPTED "):
        block = f"-----BEGIN {label}PRIVATE KEY-----\nabc\n-----END {label}PRIVATE KEY-----"
        assert redact(block) == "[REDACTED:private-key]"


def test_connection_string_password_only() -> None:
    out = redact("db: postgres://admin:S3cr3tPass@db.internal:5432/app")
    assert out == "db: postgres://admin:[REDACTED:url-credentials]@db.internal:5432/app"


def test_assignment_keeps_key_name() -> None:
    assert redact('API_KEY = "d8fK2mQ9xLp7R4vTz6wY"') == 'API_KEY = "[REDACTED:secret-assignment]"'
    assert redact("token: 9f8a7b6c5d4e3f2a1b0c9d8e") == "token: [REDACTED:secret-assignment]"
    out = redact("aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCYKEYKEY")
    assert out == "aws_secret_access_key = [REDACTED:secret-assignment]"


def test_assignment_camel_case_key() -> None:
    out = redact("githubToken: d8fK2mQ9xLp7R4vTz6wY")
    assert out == "githubToken: [REDACTED:secret-assignment]"


# --- false-positive guards --------------------------------------------------


def test_sk_prefix_in_prose_untouched() -> None:
    text = "keys with the sk- prefix are secret"
    assert redact(text) == text


def test_short_values_untouched() -> None:
    text = "token = abc123 and password: hunter2"
    assert redact(text) == text


def test_low_entropy_slug_untouched() -> None:
    text = "secret: use-the-vault-workflow"
    assert redact(text) == text


def test_placeholder_value_untouched() -> None:
    text = "api_key = your_api_key_goes_here1"
    assert redact(text) == text


def test_non_secret_key_name_untouched() -> None:
    text = "tokenizer = AutoTokenizer.from_pretrained"
    assert redact(text) == text


def test_plain_urls_untouched() -> None:
    text = "see https://example.com/path?q=1 and http://localhost:8080/callback"
    assert redact(text) == text


def test_asset_line_prose_untouched() -> None:
    text = "- claude:skill:azure-deploy — Azure Deploy: scaffold Bicep and a deploy pipeline"
    assert redact(text) == text


def test_eyj_in_prose_untouched() -> None:
    text = 'eyJ is how base64 of {" begins'
    assert redact(text) == text


def test_idempotent() -> None:
    once = redact(f"{PEM}\napi_key = 'd8fK2mQ9xLp7R4vTz6wY'\nurl postgres://a:S3cr3t9x@db\n{JWT}")
    assert redact(once) == once


# --- wiring into prompt builders --------------------------------------------


class _StubProvider:
    name = "claude"

    def __init__(self, asset: ScannedAsset) -> None:
        self._asset = asset

    def roots(self) -> list[Path]:
        return []

    def scan(self) -> list[ScannedAsset]:
        return [self._asset]

    def asset_id_for_path(self, path: Path) -> str | None:
        return None


def _asset(**overrides: object) -> ScannedAsset:
    fields: dict[str, object] = {
        "provider": "claude",
        "kind": "skill",
        "name": "deploy",
        "title": "Deploy",
        "description": "Deploys the app.",
        "path": Path("/tmp/skills/deploy/SKILL.md"),
        "updated_at": datetime.now(tz=UTC),
        "content": "",
    }
    fields.update(overrides)
    return ScannedAsset(**fields)  # type: ignore[arg-type]


def test_simulation_prompt_redacts_asset_description() -> None:
    asset = _asset(description=f"Deploys using {AWS_KEY} to S3.")
    providers: list[Provider] = [_StubProvider(asset)]
    project = Project(name="p", goal="ship it", flow_mermaid=None, asset_ids=[asset.id])
    prompt = build_prompt(project, providers, "run the deploy")
    assert AWS_KEY not in prompt
    assert "[REDACTED:aws-access-key]" in prompt


def test_diagram_prompt_redacts_path() -> None:
    asset = _asset(path=Path(f"/tmp/{GITHUB_TOKEN}/SKILL.md"))
    prompt = _diagram_prompt(asset)
    assert GITHUB_TOKEN not in prompt
    assert "[REDACTED:github-token]" in prompt


def test_chat_project_context_redacts_asset_description() -> None:
    asset = _asset(description=f"Calls the API with {SK_KEY}.")
    providers: list[Provider] = [_StubProvider(asset)]
    project = Project(name="p", goal="ship it", flow_mermaid=None, asset_ids=[asset.id])
    context = _project_context(project, providers)
    assert SK_KEY not in context
    assert "[REDACTED:sk-key]" in context
