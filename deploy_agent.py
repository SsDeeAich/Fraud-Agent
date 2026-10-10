#!/usr/bin/env python3
"""Deployment script for deploying the Compliance Agent to Vertex AI Agent Platform (Agent Engine).

Usage:
    python deploy_agent.py
    python deploy_agent.py --project customers-444618 --region us-central1
    python deploy_agent.py --display-name "Compliance Agent" --description "Fraud Detection Agent"
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("deploy_agent")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Deploy Compliance Agent to Vertex AI Agent Platform (Agent Engine)"
    )
    parser.add_argument(
        "--project",
        type=str,
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618"),
        help="Google Cloud Project ID (default: from .env or GOOGLE_CLOUD_PROJECT)",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="Google Cloud Region (default: from .env or GOOGLE_CLOUD_LOCATION)",
    )
    parser.add_argument(
        "--agent-dir",
        type=str,
        default="compliance_agent",
        help="Path to the agent directory (default: compliance_agent)",
    )
    parser.add_argument(
        "--display-name",
        type=str,
        default="Impact Compliance & Fraud Detection Agent",
        help="Display name for the agent in Agent Engine",
    )
    parser.add_argument(
        "--description",
        type=str,
        default="Root supervisor agent for compliance, fraud detection, and regulatory oversight.",
        help="Description of the agent",
    )
    parser.add_argument(
        "--agent-engine-id",
        type=str,
        default=None,
        help="Optional ID of an existing Agent Engine instance to update. If omitted (default), a brand NEW instance is created on every deployment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and dependencies without initiating remote deployment",
    )
    return parser.parse_args()

def validate_environment(project: str, region: str, agent_dir: str):
    """Validates local files, environment variables, and authentication."""
    logger.info("Validating deployment environment...")

    # Validate agent directory
    agent_path = Path(agent_dir).resolve()
    if not agent_path.exists() or not agent_path.is_dir():
        raise FileNotFoundError(f"Agent directory not found: {agent_path}")

    agent_py = agent_path / "agent.py"
    if not agent_py.exists():
        raise FileNotFoundError(f"Missing agent.py in {agent_path}")

    # Validate requirements.txt
    req_file = agent_path / "requirements.txt"
    if not req_file.exists():
        logger.warning("No requirements.txt found in %s, staging root requirements.txt", agent_path)

    # Check GCP Project & Region
    if not project:
        raise ValueError("Google Cloud Project ID must be specified.")
    if not region:
        raise ValueError("Google Cloud Region must be specified.")

    logger.info("Environment validated successfully: Project=%s, Region=%s, Agent=%s", project, region, agent_dir)

def get_python_executable() -> str:
    """Finds the python executable with google.adk installed, preferring workspace virtual environment."""
    workspace = Path(__file__).resolve().parent
    for venv_name in [".venv", "venv"]:
        venv_py = workspace / venv_name / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
    return sys.executable

def deploy_to_agent_engine(
    project: str,
    region: str,
    agent_dir: str = "compliance_agent",
    display_name: str = "Impact Compliance & Fraud Detection Agent",
    description: str = "Root supervisor agent for compliance, fraud detection, and regulatory oversight.",
    agent_engine_id: str = None,
):
    """Deploys the agent to Vertex AI Agent Platform using ADK deploy."""
    python_exe = get_python_executable()
    logger.info("Using Python executable: %s", python_exe)

    cmd = [
        python_exe,
        "-m",
        "google.adk.cli",
        "deploy",
        "agent_engine",
        agent_dir,
        f"--project={project}",
        f"--region={region}",
        f"--display_name={display_name}",
        f"--description={description}",
        "--otel_to_cloud",
    ]

    if agent_engine_id:
        cmd.append(f"--agent_engine_id={agent_engine_id}")
        cmd.append(f"--memory_service_uri=agentengine://{agent_engine_id}")
        cmd.append(f"--session_service_uri=agentengine://{agent_engine_id}")

    logger.info("Executing ADK Agent Engine deployment command:")
    logger.info("  %s", " ".join(cmd))

    env = os.environ.copy()
    env["GOOGLE_CLOUD_PROJECT"] = project
    env["GOOGLE_CLOUD_LOCATION"] = region
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    env["OTEL_SERVICE_NAME"] = "compliance_agent"
    env["OTEL_TRACES_EXPORTER"] = "gcp_trace"
    env["CONTEXT_CACHE_TTL_SECONDS"] = os.getenv("CONTEXT_CACHE_TTL_SECONDS", "3600")
    env["CONTEXT_CACHE_MIN_TOKENS"] = os.getenv("CONTEXT_CACHE_MIN_TOKENS", "2048")
    env["CONTEXT_CACHE_INTERVALS"] = os.getenv("CONTEXT_CACHE_INTERVALS", "1")
    if agent_engine_id:
        env["AGENT_ENGINE_ID"] = agent_engine_id

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )

    output_lines = []
    for line in process.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)

    process.wait()
    has_deploy_error = any(
        "Deploy failed:" in line or "Failed to deploy to Agent Platform:" in line
        for line in output_lines
    )
    if process.returncode != 0 or has_deploy_error:
        logger.error("Deployment failed! Please check error output above.")
        sys.exit(process.returncode if process.returncode != 0 else 1)

    logger.info("Deployment to Vertex AI Agent Engine completed successfully!")

def main():
    # Load .env file
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    args = parse_args()

    logger.info("=== Vertex AI Agent Engine Deployment ===")
    logger.info("Project:      %s", args.project)
    logger.info("Region:       %s", args.region)
    logger.info("Agent Dir:    %s", args.agent_dir)
    logger.info("Display Name: %s", args.display_name)
    mode_desc = f"UPDATE existing instance ({args.agent_engine_id})" if args.agent_engine_id else "CREATE BRAND NEW instance"
    logger.info("Deploy Mode:  %s", mode_desc)

    validate_environment(args.project, args.region, args.agent_dir)

    if args.dry_run:
        logger.info("[Dry Run] All validation checks passed. Ready for deployment.")
        return

    deploy_to_agent_engine(
        project=args.project,
        region=args.region,
        agent_dir=args.agent_dir,
        display_name=args.display_name,
        description=args.description,
        agent_engine_id=args.agent_engine_id,
    )

if __name__ == "__main__":
    main()
