import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

from engine.constants import DEFAULT_UK_DATASET_URI


class FakeApp:
    def __init__(self, name):
        self.name = name

    def function(self, **_kwargs):
        return lambda function: function


class FakeImage:
    def __init__(self):
        self.steps = []

    @classmethod
    def debian_slim(cls, **kwargs):
        image = cls()
        image.steps.append(("debian_slim", kwargs))
        return image

    def _step(self, name, *args, **kwargs):
        self.steps.append((name, args, kwargs))
        return self

    def apt_install(self, *args):
        return self._step("apt_install", *args)

    def pip_install_from_requirements(self, *args):
        return self._step("pip_install_from_requirements", *args)

    def run_function(self, *args):
        return self._step("run_function", *args)

    def add_local_dir(self, *args, **kwargs):
        return self._step("add_local_dir", *args, **kwargs)


class FakeSecret:
    @classmethod
    def from_name(cls, name):
        return SimpleNamespace(name=name)


def identity_decorator(**_kwargs):
    return lambda function: function


def test_modal_deployment_definition_imports_without_remote_calls(monkeypatch):
    monkeypatch.setenv("POLICYENGINE_UK_CHAT_MODAL_APP_NAME", "peukchat-test")
    monkeypatch.setenv("POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME", "peukchat-test-secrets")
    fake_modal = SimpleNamespace(
        App=FakeApp,
        Image=FakeImage,
        Secret=FakeSecret,
        asgi_app=identity_decorator,
        concurrent=identity_decorator,
    )
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    sys.modules.pop("modal_app", None)

    try:
        modal_app = importlib.import_module("modal_app")

        assert modal_app.APP_NAME == "peukchat-test"
        assert modal_app.SECRET_NAME == "peukchat-test-secrets"
        assert modal_app.DEFAULT_UK_DATASET_URI == DEFAULT_UK_DATASET_URI
        assert modal_app.app.name == "peukchat-test"
        assert modal_app.chat_secrets.name == "peukchat-test-secrets"
        assert [step[0] for step in modal_app.image.steps] == [
            "debian_slim",
            "apt_install",
            "pip_install_from_requirements",
            "run_function",
            "add_local_dir",
        ]
    finally:
        sys.modules.pop("modal_app", None)


def test_production_deploy_seeds_pinned_default_dataset_reference():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github/workflows/deploy.yml").read_text()

    assert (
        f'POLICYENGINE_UK_DEFAULT_DATASET="{DEFAULT_UK_DATASET_URI}"'
        in workflow
    )


def test_preview_deploy_seeds_credentials_and_cors_before_modal_starts():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github/workflows/pr-beta-deploy.yml").read_text()

    assert workflow.count(
        "HUGGING_FACE_TOKEN: ${{ secrets.HUGGING_FACE_TOKEN }}"
    ) == 1
    assert workflow.count('HUGGING_FACE_TOKEN="$HUGGING_FACE_TOKEN"') == 1
    assert "FRONTEND_URL: ${{ steps.names.outputs.frontend_url }}" in workflow
    assert 'HOSTNAMES="$FRONTEND_URL"' in workflow
    assert 'PUBLIC_BASE_URL="$FRONTEND_URL/uk/chat"' in workflow
    assert '-X OPTIONS "$backend_url/chat/message"' in workflow
    assert 'Access-Control-Request-Method: POST' in workflow
    assert workflow.index('modal app stop "$MODAL_APP_NAME"') < workflow.index(
        'modal secret create "$MODAL_SECRET_NAME"'
    )
    assert "Update Modal secret with preview frontend URL" not in workflow
    assert "Refresh backend preview with preview frontend URL" not in workflow
