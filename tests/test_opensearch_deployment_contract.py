"""Static deployment contracts for shared OpenSearch configuration."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCH_ENV_TO_KEY = {
    "SEARCH_BACKEND": "search-backend",
    "OPENSEARCH_URL": "opensearch-url",
    "OPENSEARCH_INDEX_NATIVE": "opensearch-index-native",
    "OPENSEARCH_INDEX_YOUTUBE": "opensearch-index-youtube",
    "OPENSEARCH_VERIFY_SSL": "opensearch-verify-ssl",
}


def _assert_shared_search_env(template: str) -> None:
    for env_name, key in SEARCH_ENV_TO_KEY.items():
        assert f"- name: {env_name}" in template
        assert f"key: {key}" in template
    for env_name, key in (
        ("OPENSEARCH_USER", "opensearch-user"),
        ("OPENSEARCH_PASSWORD", "opensearch-password"),
    ):
        env_block = template.split(f"- name: {env_name}", 1)[1].split("- name:", 1)[0]
        assert "secretKeyRef:" in env_block
        assert f"key: {key}" in env_block
        assert "optional: true" in env_block


def test_standalone_deployments_share_opensearch_config_and_optional_credentials():
    configmap = (ROOT / "k8s/configmap.yaml").read_text()
    for key in SEARCH_ENV_TO_KEY.values():
        assert f"{key}:" in configmap

    for name in ("api", "worker"):
        _assert_shared_search_env((ROOT / f"k8s/{name}-deployment.yaml").read_text())


def test_helm_deployments_share_opensearch_config_and_optional_credentials():
    configmap = (ROOT / "charts/transcript-create/templates/configmap.yaml").read_text()
    for key in SEARCH_ENV_TO_KEY.values():
        assert f"{key}:" in configmap

    for name in ("api", "worker"):
        _assert_shared_search_env((ROOT / f"charts/transcript-create/templates/deployment-{name}.yaml").read_text())
