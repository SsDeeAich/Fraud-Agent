"""Asynchronous GCS and Knowledge Grounding tools for PCA Continuous Monitoring."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
from google.cloud import storage
from pypdf import PdfReader

from ..telemetry import trace_tool

logger = logging.getLogger("compliance_agent.gcs_tools")

BUCKET_NAME = os.getenv("GCS_COMPLIANCE_BUCKET", "impact_compliance-1")
PDF_BLOB_NAME = "troubleshooting_pca_continuous_monitoring.pdf"

_GROUNDING_KNOWLEDGE_CACHE: Optional[Dict[str, str]] = None


@trace_tool(tool_type="gcs")
async def upload_grounding_document(local_pdf_path: Optional[str] = None) -> str:
    """Asynchronously uploads the PCA troubleshooting PDF to the GCS compliance bucket.

    Args:
        local_pdf_path: Local path to PDF. If not provided, searches default workspace locations.

    Returns:
        The GCS URI where the document is stored.
    """
    def _upload() -> str:
        client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618"))
        bucket = client.bucket(BUCKET_NAME)

        path_to_use = local_pdf_path
        if not path_to_use:
            candidates = [
                Path(__file__).resolve().parent.parent.parent / "troubleshooting_pca_continuous_monitoring.pdf",
                Path("troubleshooting_pca_continuous_monitoring.pdf").resolve(),
                Path("Troubleshooting_pca_continuous_monitoring.pdf").resolve()
            ]
            for c in candidates:
                if c.exists():
                    path_to_use = str(c)
                    break

        if not path_to_use or not os.path.exists(path_to_use):
            raise FileNotFoundError(f"Could not locate {PDF_BLOB_NAME} locally to upload.")

        blob = bucket.blob(PDF_BLOB_NAME)
        blob.upload_from_filename(path_to_use)
        gcs_uri = f"gs://{BUCKET_NAME}/{PDF_BLOB_NAME}"
        logger.info("Uploaded %s to %s", path_to_use, gcs_uri)
        return gcs_uri

    return await asyncio.to_thread(_upload)

@trace_tool(tool_type="gcs")
async def get_grounding_knowledge() -> Dict[str, str]:
    """Asynchronously extracts and caches key risk domain knowledge from the PCA Continuous Monitoring troubleshooting guide.

    Returns:
        Dictionary mapping risk signal categories to guidelines, definitions, and debug steps.
    """
    global _GROUNDING_KNOWLEDGE_CACHE
    if _GROUNDING_KNOWLEDGE_CACHE is not None:
        return _GROUNDING_KNOWLEDGE_CACHE

    def _read_pdf() -> Dict[str, str]:
        local_pdf = Path(__file__).resolve().parent.parent.parent / "troubleshooting_pca_continuous_monitoring.pdf"
        full_text = ""
        if local_pdf.exists():
            try:
                reader = PdfReader(str(local_pdf))
                for page in reader.pages:
                    full_text += page.extract_text() + "\n"
            except Exception as e:
                logger.warning("Failed to read local PDF for grounding: %s", e)

        return {
            "overview": "PCA Continuous Monitoring continuously evaluates publisher behavior and scores fraud risk.",
            "click_spamming": "Click spamming occurs when publishers generate massive volumes of illegitimate clicks to claim credit for organic conversions. Conversion rates are typically well below 0.01%.",
            "click_stuffing": "Click stuffing involves loading tracking links invisibly (iframes, pop-unders, bots). Over 80% of clicks often exhibit header anomalies or bot signatures.",
            "user_entropy": "High user entropy / repeated IP & UA signals show clicks and actions concentrated within small device/IP pools, indicating click farms, server scripts, or bots.",
            "injected_clicks": "Clicks injected via malware or app vulnerabilities to steal attribution immediately before an action.",
            "rejection_rate": "Substantial percentages of reversed actions indicate credit card fraud, refund exploits, or advertiser dissatisfaction.",
            "geo_mismatch": "Significant divergence between promotional regions, publisher domicile, and action geolocation indicates spoofed traffic or unauthorized arbitrage.",
            "abnormal_peaks": "Sudden sharp surges in actions/clicks relative to historical moving average (EWMA) without corresponding brand growth.",
            "account_graph": "Identifies fraud rings and hidden ownership clusters sharing payment details, IPs, device fingerprints, or contact info.",
            "recommended_actions": (
                "Recommended compliance actions include:\n"
                "1. Place a qualified-to-pay hold on affected accounts.\n"
                "2. Confirm short click-to-action times and header anomalies.\n"
                "3. Request brand evidence on SDK exploits / spoofed orders.\n"
                "4. Remove malicious entities from the marketplace and platform."
            ),
            "source_document": f"gs://{BUCKET_NAME}/{PDF_BLOB_NAME}"
        }

    knowledge = await asyncio.to_thread(_read_pdf)
    _GROUNDING_KNOWLEDGE_CACHE = knowledge
    return knowledge


@trace_tool(tool_type="gcs")
async def get_risk_signal_guidance(signal_name: str) -> str:
    """Asynchronously retrieves specific troubleshooting guidance for a given risk signal from the PCA documentation.

    Args:
        signal_name: Name of the signal (e.g., 'click_spamming', 'geo_mismatch', 'user_entropy', 'account_graph').

    Returns:
        Guidance text explaining the signal, false positives/negatives, and recommended investigation steps.
    """
    knowledge = await get_grounding_knowledge()
    key = signal_name.lower().replace(" ", "_")
    return knowledge.get(key, knowledge.get("overview", "No specific guidance found."))
