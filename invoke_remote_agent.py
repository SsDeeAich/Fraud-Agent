#!/usr/bin/env python3
"""Example client script for invoking the deployed Compliance Agent on Vertex AI Agent Engine.

Usage:
    python invoke_remote_agent.py --agent-engine-id <REASONING_ENGINE_ID> "IRFRAUD-8254, 4381629"
"""

import argparse
import os
import sys
from dotenv import load_dotenv
import vertexai

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Query deployed Vertex AI Agent Engine instance")
    parser.add_argument(
        "--project",
        type=str,
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618"),
        help="GCP Project ID",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="GCP Region",
    )
    parser.add_argument(
        "--agent-engine-id",
        type=str,
        default=os.getenv("AGENT_ENGINE_ID", "4135651909425954816"),
        help="Reasoning Engine resource ID or full resource name (default: 4135651909425954816)",
    )
    parser.add_argument(
        "query",
        type=str,
        nargs="?",
        default="IRFRAUD-8254, 4381629",
        help="Query to send to the remote agent",
    )

    args = parser.parse_args()

    print(f"Connecting to Agent Engine {args.agent_engine_id} in {args.project} ({args.region})...")
    client = vertexai.Client(project=args.project, location=args.region)

    resource_name = args.agent_engine_id
    if not resource_name.startswith("projects/"):
        resource_name = f"projects/{args.project}/locations/{args.region}/reasoningEngines/{args.agent_engine_id}"

    # Create a new remote session
    session = client.agent_engines.create_session(
        name=resource_name,
        user_id="compliance_analyst",
    )
    print(f"Session created: {session.name}")

    # Query the remote agent
    print(f"Sending query: {args.query}")
    response = client.agent_engines.run_query_job(
        name=resource_name,
        session_name=session.name,
        user_id="compliance_analyst",
        input=args.query,
    )
    print("\n--- Remote Agent Response ---")
    print(response)

if __name__ == "__main__":
    main()
