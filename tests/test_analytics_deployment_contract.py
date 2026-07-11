"""Deployment contracts for the pseudonymous analytics HMAC key."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_aws_generates_analytics_hmac_secret_independently():
    terraform = (REPO_ROOT / "terraform/aws/main.tf").read_text()

    assert 'resource "random_password" "analytics_hmac_secret"' in terraform
    assert "analytics_hmac_secret = random_password.analytics_hmac_secret.result" in terraform
    assert "analytics_hmac_secret = random_password.session_secret.result" not in terraform

    resource = re.search(
        r'resource "random_password" "analytics_hmac_secret" \{(?P<body>.*?)\n\}',
        terraform,
        re.DOTALL,
    )
    assert resource is not None
    assert re.search(r"\blength\s*=\s*(?:3[2-9]|[4-9]\d|\d{3,})\b", resource["body"])


def test_aws_injects_analytics_hmac_secret_into_api_tasks():
    terraform = (REPO_ROOT / "terraform/aws/main.tf").read_text()

    assert 'ANALYTICS_HMAC_SECRET = module.secrets.secret_arns["analytics_hmac_secret"]' in terraform
