import importlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
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
        assert "migrate" in modal_app.app.function_options
        assert "web" in modal_app.app.function_options
        assert modal_app.WEB_MEMORY_MIB == 16_384
        assert modal_app.app.function_options["web"]["memory"] == 16_384
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


def test_local_docker_uses_the_single_chat_runtime():
    env_example = (REPO_ROOT / ".env.example").read_text()
    compose = (REPO_ROOT / "docker-compose.yml").read_text()

    assert "UK_CHAT_RUNTIME" not in env_example
    assert "UK_CHAT_RUNTIME" not in compose


def test_database_migrations_run_before_local_and_modal_backends():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    modal_app = (REPO_ROOT / "modal_app.py").read_text()
    production = (REPO_ROOT / ".github/workflows/deploy.yml").read_text()
    preview = (REPO_ROOT / ".github/workflows/pr-beta-deploy.yml").read_text()
    sync_script = (REPO_ROOT / ".github/scripts/sync-modal-secret.sh").read_text()

    assert 'command: ["alembic", "-c", "alembic.ini", "upgrade", "head"]' in compose
    assert "condition: service_completed_successfully" in compose
    assert "ALEMBIC_DATABASE_URL=" in compose
    assert "def migrate():" in modal_app
    assert 'command.upgrade(Config("/app/backend/alembic.ini"), "head")' in modal_app
    assert production.index(".github/scripts/run-modal-migration.sh") < production.index(
        "modal deploy modal_app.py"
    )
    assert preview.index(".github/scripts/run-modal-migration.sh") < preview.index(
        ".github/scripts/deploy-modal-preview.sh"
    )
    assert '"ALEMBIC_DATABASE_URL=$ALEMBIC_DATABASE_URL"' in sync_script


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
    assert sync_script.count('"HUGGING_FACE_TOKEN=$HUGGING_FACE_TOKEN"') == 1
    assert workflow.count("UK_CHAT_EVAL_TOKEN: ${{ secrets.UK_CHAT_EVAL_TOKEN }}") == 1
    assert sync_script.count('"UK_CHAT_EVAL_TOKEN=$UK_CHAT_EVAL_TOKEN"') == 1
    assert '"BILLING_ENABLED=$billing_enabled"' in sync_script
    assert 'if [[ "$billing_enabled" == "true" ]]' in sync_script
    assert "HOSTNAMES: ${{ steps.names.outputs.frontend_url }}" in workflow
    assert (
        "PUBLIC_BASE_URL: ${{ steps.names.outputs.frontend_url }}/uk/chat" in workflow
    )
    assert '"HOSTNAMES=$HOSTNAMES"' in sync_script
    assert '"PUBLIC_BASE_URL=$PUBLIC_BASE_URL"' in sync_script
    assert '-X OPTIONS "$backend_url/chat/message"' in smoke_script
    assert "Access-Control-Request-Method: POST" in smoke_script
    assert workflow.index(".github/scripts/stop-modal-app.sh") < workflow.index(
        ".github/scripts/sync-modal-secret.sh"
    )
    assert "Update Modal secret with preview frontend URL" not in workflow
    assert "Refresh backend preview with preview frontend URL" not in workflow


def _run_modal_preview_cleanup(
    tmp_path,
    app_list,
    *,
    list_exit_code=0,
    stop_exit_code=0,
):
    calls_path = tmp_path / "modal-calls"
    fake_modal = tmp_path / "modal"
    fake_modal.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$MODAL_CALLS_FILE"\n'
        'if [[ "$*" == "app list --json" ]]; then\n'
        '  printf "%s\\n" "$MODAL_APP_LIST_JSON"\n'
        '  exit "$MODAL_LIST_EXIT_CODE"\n'
        "fi\n"
        'if [[ "$1 $2" == "app stop" ]]; then\n'
        '  exit "$MODAL_STOP_EXIT_CODE"\n'
        "fi\n"
        "exit 0\n"
    )
    fake_modal.chmod(0o755)
    environment = {
        "MODAL_APP_NAME": "pe-uk-chat-123",
        "MODAL_SECRET_NAME": "pe-uk-chat-123-secrets",
        "MODAL_APP_LIST_JSON": app_list,
        "MODAL_CALLS_FILE": str(calls_path),
        "MODAL_LIST_EXIT_CODE": str(list_exit_code),
        "MODAL_STOP_EXIT_CODE": str(stop_exit_code),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [REPO_ROOT / ".github/scripts/cleanup-modal-preview.sh"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = calls_path.read_text().splitlines()
    return result, calls


def test_modal_preview_cleanup_stops_app_before_deleting_secret(tmp_path):
    result, calls = _run_modal_preview_cleanup(
        tmp_path,
        '[{"description":"pe-uk-chat-123","state":"deployed"}]',
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "app list --json",
        "app stop pe-uk-chat-123 --yes",
        "secret delete pe-uk-chat-123-secrets --yes --allow-missing",
    ]


def test_modal_preview_cleanup_deletes_secret_when_app_is_missing(tmp_path):
    result, calls = _run_modal_preview_cleanup(tmp_path, "[]")

    assert result.returncode == 0, result.stderr
    assert calls == [
        "app list --json",
        "secret delete pe-uk-chat-123-secrets --yes --allow-missing",
    ]


def test_modal_preview_cleanup_deletes_secret_when_app_is_stopped(tmp_path):
    result, calls = _run_modal_preview_cleanup(
        tmp_path,
        '[{"description":"pe-uk-chat-123","state":"stopped"}]',
    )

    assert result.returncode == 0, result.stderr
    assert calls == [
        "app list --json",
        "secret delete pe-uk-chat-123-secrets --yes --allow-missing",
    ]


def test_modal_preview_cleanup_keeps_secret_when_app_stop_fails(tmp_path):
    result, calls = _run_modal_preview_cleanup(
        tmp_path,
        '[{"description":"pe-uk-chat-123","state":"deployed"}]',
        stop_exit_code=1,
    )

    assert result.returncode != 0
    assert calls == [
        "app list --json",
        "app stop pe-uk-chat-123 --yes",
    ]


def test_modal_preview_cleanup_keeps_secret_when_app_lookup_fails(tmp_path):
    result, calls = _run_modal_preview_cleanup(
        tmp_path,
        "[]",
        list_exit_code=1,
    )

    assert result.returncode != 0
    assert calls == ["app list --json"]


def test_preview_cleanup_uses_trusted_default_branch_scripts():
    workflow = (REPO_ROOT / ".github/workflows/pr-beta-deploy.yml").read_text()
    cleanup = workflow.split("  cleanup:", maxsplit=1)[1]

    assert "ref: ${{ github.event.repository.default_branch }}" in cleanup
    assert "github.event.pull_request.head.sha" not in cleanup


def test_modal_secret_sync_omits_billing_credentials_when_disabled(tmp_path):
    args_path = tmp_path / "modal-args"
    fake_modal = tmp_path / "modal"
    fake_modal.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" > "$MODAL_ARGS_FILE"\n'
    )
    fake_modal.chmod(0o755)

    environment = {
        "MODAL_SECRET_NAME": "test-secret",
        "ANTHROPIC_API_KEY": "anthropic",
        "UK_CHAT_EVAL_TOKEN": "eval-token",
        "POLICYENGINE_UK_DATA_TOKEN": "uk-data",
        "HUGGING_FACE_TOKEN": "hugging-face",
            "DATABASE_URL": "postgresql://example",
            "ALEMBIC_DATABASE_URL": "postgresql://migration-example",
        "BILLING_ENABLED": "false",
        "OBSERVABILITY_ENVIRONMENT": "test",
        "OBSERVABILITY_GOOGLE_CLOUD_PROJECT": "project",
        "OBSERVABILITY_GOOGLE_WORKLOAD_IDENTITY_PROVIDER": "provider",
        "OBSERVABILITY_GOOGLE_SERVICE_ACCOUNT_EMAIL": "service@example.com",
        "OBSERVABILITY_LOG_DESTINATIONS": "stdout",
        "HOSTNAMES": "https://chat.example",
        "PUBLIC_BASE_URL": "https://chat.example/uk/chat",
        "SUPABASE_URL": "https://must-not-be-copied.example",
        "SUPABASE_SERVICE_ROLE_KEY": "must-not-be-copied",
        "STRIPE_SECRET_KEY": "must-not-be-copied",
        "STRIPE_WEBHOOK_SECRET": "must-not-be-copied",
        "MODAL_ARGS_FILE": str(args_path),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [REPO_ROOT / ".github/scripts/sync-modal-secret.sh"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    args = args_path.read_text().splitlines()
    assert "BILLING_ENABLED=false" in args
    assert not any(argument.startswith("SUPABASE_") for argument in args)
    assert not any(argument.startswith("STRIPE_") for argument in args)


def test_modal_secret_sync_requires_credentials_when_billing_is_enabled(
    tmp_path,
):
    environment = {
        "MODAL_SECRET_NAME": "test-secret",
        "ANTHROPIC_API_KEY": "anthropic",
        "UK_CHAT_EVAL_TOKEN": "eval-token",
        "POLICYENGINE_UK_DATA_TOKEN": "uk-data",
        "HUGGING_FACE_TOKEN": "hugging-face",
            "DATABASE_URL": "postgresql://example",
            "ALEMBIC_DATABASE_URL": "postgresql://migration-example",
        "BILLING_ENABLED": "true",
        "OBSERVABILITY_ENVIRONMENT": "test",
        "OBSERVABILITY_GOOGLE_CLOUD_PROJECT": "project",
        "OBSERVABILITY_GOOGLE_WORKLOAD_IDENTITY_PROVIDER": "provider",
        "OBSERVABILITY_GOOGLE_SERVICE_ACCOUNT_EMAIL": "service@example.com",
        "OBSERVABILITY_LOG_DESTINATIONS": "stdout",
        "HOSTNAMES": "https://chat.example",
        "PUBLIC_BASE_URL": "https://chat.example/uk/chat",
    }

    result = subprocess.run(
        [REPO_ROOT / ".github/scripts/sync-modal-secret.sh"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SUPABASE_URL is required when billing is enabled" in result.stderr


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
        assert "GATEWAY_PROPOSAL_SIGNING_KEY" not in workflow
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
