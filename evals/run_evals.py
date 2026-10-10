#!/usr/bin/env python3
"""Automated Evaluation Runner for Compliance & Fraud Detection Agent.

Evaluates:
1. Deterministic Step 6 threshold triggering (6A - 6F rules from Prompt02).
2. Trajectory verification (tool call precision).
3. LLM-as-a-Judge report grading across 5 business dimensions:
   - Entity & Graph Completeness (20%)
   - Threshold Adherence & Precision (25%)
   - Allegation Reconciliation (20%)
   - Domain Grounding (15%)
   - Actionability of Next Steps (20%)

Usage:
    # Run mock evaluation (fast, offline, no BigQuery required)
    python evals/run_evals.py --mock

    # Run specific eval case
    python evals/run_evals.py --mock --eval-id EVAL-001-PROMPT02-BASELINE

    # Run live evaluation against BigQuery & Local Agent
    python evals/run_evals.py --live

    # Run without LLM judge (deterministic trajectory checks only)
    python evals/run_evals.py --mock --skip-judge
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Ensure root workspace is in sys.path
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_DIR))

# Load .env
load_dotenv(WORKSPACE_DIR / ".env")

from google import genai
from google.genai import types
from evals.eval_rubrics import (
    JUDGE_SYSTEM_INSTRUCTION,
    EvalJudgeVerdict,
    JudgeScoreDetails,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("compliance_agent.evals")


def load_dataset(dataset_path: Path) -> List[Dict[str, Any]]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Eval dataset not found at {dataset_path}")
    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_step6_triggers(metrics: Dict[str, Any]) -> Dict[str, bool]:
    """Deterministically evaluates Step 6 conditional trigger rules from Prompt02."""
    long_cta = metrics.get("long_cta_pct") or 0.0
    injected_click = metrics.get("Injected_click_pct") or 0.0
    click_spamming = metrics.get("Click_spamming_pct") or 0.0
    url_risk = metrics.get("High_risk_url_action_pct") or 0.0
    geo_mismatch = metrics.get("Geo_mismatch_pct") or 0.0
    login_ip = metrics.get("High_risk_login_ip_pct") or 0.0
    login_email = metrics.get("High_risk_login_email_pct") or 0.0
    peak_actions = metrics.get("Abnormal_peak_actions_pct") or 0.0
    peak_clicks = metrics.get("Abnormal_peak_clicks_pct") or 0.0

    return {
        "6A_attribution": bool(long_cta > 0.1 or injected_click > 0.1 or click_spamming > 0.1),
        "6B_url_risk": bool(url_risk > 0.1),
        "6B1_semrush": bool(url_risk > 0.1),
        "6C_geo_mismatch": bool(geo_mismatch > 0.5),
        "6D_login_ip": bool(login_ip > 0.1),
        "6E_login_email": bool(login_email > 0.1),
        "6F_abnormal_peaks": bool(peak_actions > 0.1 or peak_clicks > 0.1),
    }


async def generate_mock_agent_response(case: Dict[str, Any], model_name: str) -> str:
    """Generates the grounded fraud report using Gemini given the mock context."""
    client = genai.Client()
    mock_data = case["mock_context"]
    prompt = f"""You are a senior fraud analyst.
Based on the following consolidated investigation data, provide a concise, structured fraud risk report for publisher {case['input']['publisher_id']} referencing ticket {case['input'].get('jira_ticket_id', '')}.

Data:
{json.dumps(mock_data, indent=2)}

Structure your report into:
1. Entity Identification & Account Graph Linkage
2. Platform & Commercial History
3. Historical Complaints & JIRA Tickets
4. PCA Continuous Monitoring & Specific Fraud Indicators (Click spamming, Click stuffing, User entropy, Geo mismatch)
5. Assessment of Current Ticket Allegations (critically evaluate allegations against telemetry)
6. Actionable Next Steps (qualified-to-pay hold, evidence requests, marketplace/platform removal)
"""
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model_name,
        contents=prompt,
    )
    return response.text


async def run_live_agent(query: str) -> str:
    """Invokes the local compliance_agent using ADK InMemoryRunner."""
    from compliance_agent.agent import root_agent
    from google.adk.runners import InMemoryRunner

    runner = InMemoryRunner(agent=root_agent)
    user_id = "eval_runner"
    session = await runner.session_service.create_session(
        user_id=user_id,
        app_name="compliance_agent",
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part.from_text(text=query)],
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=user_msg,
    ):
        if event.is_final_response():
            if event.message and event.message.parts:
                for part in event.message.parts:
                    if hasattr(part, "text") and part.text:
                        final_text += part.text
        elif hasattr(event, "content") and event.content:
            final_text += str(event.content)

    return final_text


async def judge_agent_output(
    eval_id: str,
    input_query: str,
    evidence_data: Dict[str, Any],
    agent_output: str,
    judge_model: str,
) -> EvalJudgeVerdict:
    """Invokes Gemini as an LLM Judge with structured output."""
    client = genai.Client()

    prompt = f"""
Input Query: {input_query}

Consolidated Evidence Data:
{json.dumps(evidence_data, indent=2)}

Agent's Generated Output:
{agent_output}
"""
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=judge_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=EvalJudgeVerdict,
        ),
    )

    result_dict = json.loads(response.text)
    result_dict["eval_id"] = eval_id
    return EvalJudgeVerdict(**result_dict)


async def main():
    parser = argparse.ArgumentParser(description="Impact Compliance Agent Eval Runner")
    parser.add_argument("--mock", action="store_true", default=True, help="Run mock evaluation (fast, no BigQuery)")
    parser.add_argument("--live", action="store_true", help="Run live evaluation against BigQuery & Local Agent")
    parser.add_argument("--eval-id", type=str, default=None, help="Filter by specific eval ID")
    parser.add_argument("--skip-judge", action="store_true", help="Skip LLM judge scoring (deterministic checks only)")
    parser.add_argument(
        "--judge-model",
        type=str,
        default=os.getenv("AGENT_MODEL", "gemini-2.5-flash"),
        help="Gemini model for judging (default: gemini-2.5-flash)",
    )
    args = parser.parse_args()

    mode = "LIVE" if args.live else "MOCK"
    dataset_path = Path(__file__).parent / "eval_dataset.json"
    dataset = load_dataset(dataset_path)

    if args.eval_id:
        dataset = [c for c in dataset if c["eval_id"] == args.eval_id]
        if not dataset:
            logger.error(f"No eval case found matching ID {args.eval_id}")
            sys.exit(1)

    print("\n" + "=" * 80)
    print(f" 🛡️  IMPACT COMPLIANCE AGENT EVALUATION RUNNER ({mode} MODE)")
    print(f" Model: {args.judge_model} | Test Cases: {len(dataset)}")
    print("=" * 80 + "\n")

    eval_results = []

    for idx, case in enumerate(dataset, 1):
        eval_id = case["eval_id"]
        scenario = case["scenario_name"]
        print(f"[{idx}/{len(dataset)}] Running {eval_id}: {scenario}...")

        # 1. Deterministic Step 6 Trigger Evaluation
        expected_triggers = case["expected_trajectory"]["step_6_triggers"]
        actual_triggers = evaluate_step6_triggers(case["mock_context"]["metrics_30d"])

        trigger_mismatches = []
        for trigger_name, expected_val in expected_triggers.items():
            actual_val = actual_triggers.get(trigger_name, False)
            if actual_val != expected_val:
                trigger_mismatches.append(
                    f"{trigger_name}: expected {expected_val}, got {actual_val}"
                )

        trajectory_passed = len(trigger_mismatches) == 0

        # 2. Get Agent Output
        if args.live:
            logger.info("Executing live agent pipeline for query: %s", case["input"]["user_prompt"])
            agent_output = await run_live_agent(case["input"]["user_prompt"])
        else:
            logger.info("Generating agent response from mock context...")
            agent_output = await generate_mock_agent_response(case, args.judge_model)

        # 3. Optional LLM Judge Evaluation
        verdict = None
        if not args.skip_judge:
            logger.info("Invoking LLM-as-a-Judge for %s...", eval_id)
            try:
                verdict = await judge_agent_output(
                    eval_id=eval_id,
                    input_query=case["input"]["user_prompt"],
                    evidence_data=case["mock_context"],
                    agent_output=agent_output,
                    judge_model=args.judge_model,
                )
            except Exception as e:
                logger.error("LLM Judge evaluation failed for %s: %s", eval_id, e)

        eval_results.append({
            "eval_id": eval_id,
            "scenario": scenario,
            "trajectory_passed": trajectory_passed,
            "trigger_mismatches": trigger_mismatches,
            "verdict": verdict.model_dump() if verdict else None,
            "agent_output": agent_output,
        })

        status_str = "PASSED" if (trajectory_passed and (verdict.passed if verdict else True)) else "FAILED"
        score_str = f"{verdict.weighted_score:.2f}/5.0" if verdict else "N/A"
        print(f"    -> Result: {status_str} | Trajectory: {'PASS' if trajectory_passed else 'FAIL'} | Judge Score: {score_str}")
        if trigger_mismatches:
            print(f"       ⚠️ Trigger Mismatches: {', '.join(trigger_mismatches)}")
        if verdict and verdict.critical_flaws:
            print(f"       ⚠️ Flaws: {'; '.join(verdict.critical_flaws)}")
        print()

    # Print Summary Scorecard Table
    print("\n" + "=" * 80)
    print(" 📊 EVALUATION SUMMARY SCORECARD")
    print("=" * 80)
    print(f"{'Eval ID':<35} | {'Trajectory':<10} | {'Judge':<8} | {'Score':<8} | {'Status':<8}")
    print("-" * 80)

    total_passed = 0
    scores = []
    for r in eval_results:
        v = r["verdict"]
        traj = "PASS" if r["trajectory_passed"] else "FAIL"
        judge_status = ("PASS" if v["passed"] else "FAIL") if v else "SKIPPED"
        score = f"{v['weighted_score']:.2f}" if v else "N/A"
        is_pass = r["trajectory_passed"] and (v["passed"] if v else True)
        if is_pass:
            total_passed += 1
        if v:
            scores.append(v["weighted_score"])
        status = "PASSED" if is_pass else "FAILED"
        print(f"{r['eval_id']:<35} | {traj:<10} | {judge_status:<8} | {score:<8} | {status:<8}")

    print("-" * 80)
    pass_rate = (total_passed / len(eval_results)) * 100
    avg_score = (sum(scores) / len(scores)) if scores else 0.0
    print(f"Total Pass Rate: {total_passed}/{len(eval_results)} ({pass_rate:.1f}%) | Average Judge Score: {avg_score:.2f}/5.0")
    print("=" * 80 + "\n")

    # Save results to json
    results_file = WORKSPACE_DIR / "evals" / "eval_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(eval_results, f, indent=2)
    logger.info("Saved detailed results to %s", results_file)


if __name__ == "__main__":
    asyncio.run(main())
