import importlib
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

from engine.constants import UK_CHAT_DATASET


REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeApp:
    def __init__(self, name):
        self.name = name
        self.function_options = {}

    def function(self, **kwargs):
        def decorate(function):
            self.function_options[function.__name__] = kwargs
            return function

        return decorate

    def local_entrypoint(self, **_kwargs):
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

    def pip_install(self, *args):
        return self._step("pip_install", *args)

    def add_local_dir(self, *args, **kwargs):
        return self._step("add_local_dir", *args, **kwargs)


class FakeSecret:
    @classmethod
    def from_name(cls, name):
        return SimpleNamespace(name=name)


class FakeVolume:
    @classmethod
    def from_name(cls, name, **kwargs):
        return SimpleNamespace(name=name, options=kwargs)


def identity_decorator(**_kwargs):
    return lambda function: function


def test_modal_deployment_definition_imports_without_remote_calls(monkeypatch):
    monkeypatch.setenv("POLICYENGINE_UK_CHAT_MODAL_APP_NAME", "peukchat-test")
    monkeypatch.setenv(
        "POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME", "peukchat-test-secrets"
    )
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
        assert modal_app.app.name == "peukchat-test"
        assert modal_app.chat_secrets.name == "peukchat-test-secrets"
        assert [step[0] for step in modal_app.image.steps] == [
            "debian_slim",
            "apt_install",
            "pip_install_from_requirements",
            "add_local_dir",
        ]
    finally:
        sys.modules.pop("modal_app", None)


def test_modal_eval_app_fans_out_twenty_cases_with_a_25_container_cap(
    monkeypatch,
):
    monkeypatch.setenv(
        "POLICYENGINE_UK_CHAT_EVAL_MODAL_APP_NAME",
        "pe-uk-chat-evals-test",
    )
    monkeypatch.setenv(
        "POLICYENGINE_UK_CHAT_EVAL_MODAL_SECRET_NAME",
        "pe-uk-chat-test-secrets",
    )
    fake_modal = SimpleNamespace(
        App=FakeApp,
        Image=FakeImage,
        Secret=FakeSecret,
        Volume=FakeVolume,
    )
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    sys.modules.pop("modal_eval_app", None)

    try:
        modal_eval_app = importlib.import_module("modal_eval_app")

        options = modal_eval_app.app.function_options["evaluate_case"]
        assert modal_eval_app.APP_NAME == "pe-uk-chat-evals-test"
        assert modal_eval_app.SECRET_NAME == "pe-uk-chat-test-secrets"
        assert options["max_containers"] == 25
        assert options["timeout"] == 1_800
        assert options["volumes"] == {
            modal_eval_app.REPORT_MOUNT: modal_eval_app.report_volume
        }
        assert modal_eval_app.report_volume.options == {
            "create_if_missing": True,
            "version": 2,
        }
        source = (REPO_ROOT / "modal_eval_app.py").read_text()
        assert "evaluate_case.spawn_map(" in source
        assert "concurrency=3" in source
    finally:
        sys.modules.pop("modal_eval_app", None)


def test_dataset_reference_is_not_deployment_configuration():
    workflow = (REPO_ROOT / ".github/workflows/deploy.yml").read_text()
    modal_app = (REPO_ROOT / "modal_app.py").read_text()
    env_example = (REPO_ROOT / ".env.example").read_text()
    compose = (REPO_ROOT / "docker-compose.yml").read_text()

    for content in (workflow, modal_app, env_example, compose):
        assert "POLICYENGINE_UK_DEFAULT_DATASET" not in content
        assert UK_CHAT_DATASET.uri not in content


def test_local_docker_exposes_enhanced_frs_credentials():
    env_example = (REPO_ROOT / ".env.example").read_text()
    compose = (REPO_ROOT / "docker-compose.yml").read_text()

    assert "HUGGING_FACE_TOKEN=your_token_here" in env_example
    assert "HUGGING_FACE_TOKEN=${HUGGING_FACE_TOKEN}" in compose


def test_preview_deploy_seeds_credentials_and_cors_before_modal_starts():
    workflow = (REPO_ROOT / ".github/workflows/pr-beta-deploy.yml").read_text()
    sync_script = (REPO_ROOT / ".github/scripts/sync-modal-secret.sh").read_text()
    deploy_script = (REPO_ROOT / ".github/scripts/deploy-modal-preview.sh").read_text()
    smoke_script = (
        REPO_ROOT / ".github/scripts/smoke-test-modal-backend.sh"
    ).read_text()

    assert (
        "MODAL_PREVIEW_APP_NAME: pe-uk-chat-${{ github.event.pull_request.number }}"
        in workflow
    )
    assert (
        "MODAL_PREVIEW_SECRET_NAME: "
        "pe-uk-chat-${{ github.event.pull_request.number }}-secrets" in workflow
    )
    assert "peukchat-$branch_slug" not in workflow
    assert (
        'modal.Function.from_name(os.environ["MODAL_APP_NAME"], "web")' in deploy_script
    )
    assert "BACKEND_URL: ${{ steps.modal_deploy.outputs.modal_url }}" in workflow
    assert workflow.count("HUGGING_FACE_TOKEN: ${{ secrets.HUGGING_FACE_TOKEN }}") == 1
    assert sync_script.count('HUGGING_FACE_TOKEN="$HUGGING_FACE_TOKEN"') == 1
    assert workflow.count("UK_CHAT_EVAL_TOKEN: ${{ secrets.UK_CHAT_EVAL_TOKEN }}") == 1
    assert sync_script.count('UK_CHAT_EVAL_TOKEN="$UK_CHAT_EVAL_TOKEN"') == 1
    assert "HOSTNAMES: ${{ steps.names.outputs.frontend_url }}" in workflow
    assert (
        "PUBLIC_BASE_URL: ${{ steps.names.outputs.frontend_url }}/uk/chat" in workflow
    )
    assert 'HOSTNAMES="$HOSTNAMES"' in sync_script
    assert 'PUBLIC_BASE_URL="$PUBLIC_BASE_URL"' in sync_script
    assert '-X OPTIONS "$backend_url/chat/message"' in smoke_script
    assert "Access-Control-Request-Method: POST" in smoke_script
    assert workflow.index(".github/scripts/stop-modal-app.sh") < workflow.index(
        ".github/scripts/sync-modal-secret.sh"
    )
    assert "Update Modal secret with preview frontend URL" not in workflow
    assert "Refresh backend preview with preview frontend URL" not in workflow


def test_preview_frontend_and_modal_share_pr_number_contract():
    workflow = (REPO_ROOT / ".github/workflows/pr-beta-deploy.yml").read_text()
    frontend_backend_url = (
        REPO_ROOT / "frontend/src/app/api/proxy/backend-url.ts"
    ).read_text()

    assert (
        "MODAL_PREVIEW_APP_NAME: pe-uk-chat-${{ github.event.pull_request.number }}"
        in workflow
    )
    assert "VERCEL_GIT_PULL_REQUEST_ID" in frontend_backend_url
    assert "VERCEL_GIT_COMMIT_REF" not in frontend_backend_url
    assert (
        "https://policyengine--pe-uk-chat-${pullRequestNumber}-web.modal.run"
        in frontend_backend_url
    )


def test_workflows_delegate_multiline_shell_to_repository_scripts():
    workflow_paths = sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))

    assert workflow_paths
    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text()
        assert re.search(r"^\s+run:\s*[|>]", workflow, re.MULTILINE) is None, (
            f"{workflow_path.name} contains inline multiline shell"
        )
        for script_name in re.findall(
            r"\.github/scripts/[A-Za-z0-9_.-]+",
            workflow,
        ):
            script_path = REPO_ROOT / script_name
            assert script_path.is_file(), (
                f"{workflow_path.name} references missing {script_name}"
            )
            assert os.access(script_path, os.X_OK), f"{script_name} must be executable"


def test_deploy_workflows_reuse_modal_secret_and_smoke_test_scripts():
    production = (REPO_ROOT / ".github/workflows/deploy.yml").read_text()
    preview = (REPO_ROOT / ".github/workflows/pr-beta-deploy.yml").read_text()

    for workflow in (production, preview):
        assert "run: .github/scripts/sync-modal-secret.sh" in workflow
        assert "run: .github/scripts/smoke-test-modal-backend.sh" in workflow
        assert workflow.count(
            "UK_CHAT_EVAL_TOKEN: ${{ secrets.UK_CHAT_EVAL_TOKEN }}"
        ) == 1


def test_manual_deployed_eval_workflow_is_token_safe_and_uploads_reports():
    workflow = (
        REPO_ROOT / ".github/workflows/eval-uk-population.yml"
    ).read_text()
    runner = (REPO_ROOT / ".github/scripts/run-deployed-evals.sh").read_text()

    assert "workflow_dispatch:" in workflow
    assert "timeout-minutes: 180" in workflow
    assert "EVAL_RUN_TOKEN: ${{ secrets.UK_CHAT_EVAL_TOKEN }}" in workflow
    assert "run: .github/scripts/run-deployed-evals.sh" in workflow
    assert "if: always()" in workflow
    assert "evals/reports" in workflow
    assert "--trial-timeout-seconds" in runner
    assert "--concurrency" in runner
    assert "--token" not in runner


def test_preview_frontend_url_script_writes_github_outputs(tmp_path, monkeypatch):
    script_path = REPO_ROOT / ".github/scripts/preview_frontend_url.py"
    spec = importlib.util.spec_from_file_location(
        "preview_frontend_url",
        script_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("BRANCH_NAME", "Agent/Explicit__Decile Income Concepts")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    module.main()

    assert module.slugify_branch("Agent/Explicit__Decile Income Concepts") == (
        "agent-explicit-decile-income-concepts"
    )
    assert output_path.read_text().splitlines() == [
        "branch_slug=agent-explicit-decile-income-concepts",
        "frontend_url=https://policyengine-uk-chat-git-"
        "agent-explicit-decile-income-concepts-policy-engine.vercel.app",
    ]
