"""Pluggable source connectors for corpus ingestion.

Every connector normalises whatever it reads into a `SourceDocument`. From
that point the pipeline is identical regardless of origin:

    connector.discover()  ->  SourceDocument
                          ->  redaction gate  (shared.redaction)
                          ->  sanitised bucket + sidecar
                          ->  one Bedrock KB  ->  S3 Vectors

There is deliberately one knowledge base, not one per source. The existing
sidecar `source_type` field discriminates at query time and the retriever
already filters on it, so adding a source adds configuration rather than
infrastructure.

Why not the native Bedrock KB connectors
----------------------------------------
Bedrock KB ships first-party connectors for SharePoint, Confluence, Salesforce
and web crawl. They ingest *straight into the KB*, which means they bypass the
redaction gate entirely -- the first time the raw text is addressable is after
chunking, inside the managed pipeline.

There is a hook there (`customTransformationConfiguration`), but it only
supports `stepToApply: POST_CHUNKING`, and the service writes parsed documents
to an intermediate S3 bucket *before* invoking the Lambda. So with a native
connector the un-redacted text does land in an S3 object, however briefly.

That is a real tradeoff rather than a blocker:

  Path A (this module, used for the POC)
      Redact before anything enters the KB pipeline. Raw text never reaches
      Bedrock. Strongest control, and the one to demo.

  Path B (native connector + POST_CHUNKING Lambda)
      Available when a source is too large or too incrementally-updated to
      pull through Path A. Requires: CMK on the intermediate bucket, a 24-hour
      lifecycle rule, and a bucket policy denying every principal except the
      KB role and the transformation Lambda. Weaker, but bounded.

Pick per source via `ingest_path` in config. Do not let it default silently.
"""
from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

__all__ = [
    "SourceDocument",
    "SourceConnector",
    "GoldenQuestionnaireConnector",
    "S3PrefixConnector",
    "ConfluenceConnector",
    "SharePointConnector",
    "TrustPortalConnector",
    "build_connector",
    "REGISTRY",
]


@dataclass
class SourceDocument:
    """Normalised unit of ingestion.

    `content` is raw at this point -- it has not been through the redaction
    gate. Nothing may write a SourceDocument to the sanitised bucket directly.
    """

    document_id: str
    content: str
    source_type: str           # golden_qa | compliance_cert | product_doc | prior_rfp
    source_id: str             # which configured source produced this
    mime_type: str = "text/markdown"
    updated_at: str = ""
    topic_ids: list[str] = field(default_factory=list)
    # Surfaces the connector already knows are sensitive (e.g. a customer name
    # carried in a structured field). Seeded into the dictionary pass so we do
    # not rely on detection for things the source told us outright.
    known_sensitive: list[str] = field(default_factory=list)
    extra_metadata: dict = field(default_factory=dict)

    def sidecar(self) -> dict:
        """Bedrock KB sidecar. Keys stay well under the S3 Vectors 2 KB
        filterable-metadata budget; the two large Bedrock-managed keys are
        marked non-filterable in the KB stack."""
        attrs = {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "updated_at": self.updated_at or datetime.now(timezone.utc).isoformat(),
            "topic_ids": self.topic_ids,
            "redacted": True,
        }
        attrs.update(self.extra_metadata)
        return {"metadataAttributes": attrs}


class SourceConnector(abc.ABC):
    """Base class. Implement `discover()`; the framework handles the rest."""

    #: registry key used in config
    type_name: str = ""

    def __init__(self, source_id: str, config: dict) -> None:
        self.source_id = source_id
        self.config = config
        self.source_type: str = config.get("source_type", "product_doc")
        self.redaction_profile: str = config.get("redaction_profile", "strict")
        self.ingest_path: str = config.get("ingest_path", "gate")  # gate | native

    @abc.abstractmethod
    def discover(self) -> Iterator[SourceDocument]:
        """Yield every document this source currently exposes."""

    def health_check(self) -> tuple[bool, str]:
        """Cheap reachability probe, called before a full sync so a
        misconfigured source fails in seconds rather than mid-ingest."""
        return True, "ok"

    @staticmethod
    def _stable_id(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Live for the POC
# ---------------------------------------------------------------------------


class GoldenQuestionnaireConnector(SourceConnector):
    """The curated golden dataset -- the POC's single source of truth.

    Expects one JSON object per file:

        {
          "question": "Describe your encryption at rest.",
          "answer":   "All customer data is encrypted with AES-256 ...",
          "topic_ids": ["encryption", "data_protection"],
          "approved_by": "...",
          "approved_at": "2026-03-11T00:00:00Z",
          "evidence_refs": ["soc2_2025_cc6.1"],
          "customer": "Acme Financial Corp"      # optional, seeded as sensitive
        }

    Q&A pairs are emitted as a single chunkable unit rather than split, because
    a retrieved answer without its question is close to useless for grounding
    -- the question carries the scope the answer assumes.
    """

    type_name = "golden_questionnaire"

    def discover(self) -> Iterator[SourceDocument]:
        root = Path(self.config["path"])
        if not root.exists():
            raise FileNotFoundError(f"golden dataset path not found: {root}")

        for path in sorted(root.rglob("*.json")):
            if path.name.endswith(".metadata.json"):
                continue
            record = json.loads(path.read_text())

            question = record.get("question", "").strip()
            answer = record.get("answer", "").strip()
            if not question or not answer:
                continue

            body = f"## {question}\n\n{answer}\n"
            evidence = record.get("evidence_refs") or []
            if evidence:
                body += f"\nEvidence: {', '.join(evidence)}\n"

            sensitive = [s for s in (record.get("customer"),) if s]
            sensitive += record.get("known_sensitive", [])

            yield SourceDocument(
                document_id=record.get("id") or self._stable_id(path.name, question),
                content=body,
                source_type=self.source_type,
                source_id=self.source_id,
                updated_at=record.get("approved_at", ""),
                topic_ids=record.get("topic_ids", []),
                known_sensitive=sensitive,
                extra_metadata={
                    "approved_at": record.get("approved_at", ""),
                    "has_evidence": bool(evidence),
                },
            )

    def health_check(self) -> tuple[bool, str]:
        root = Path(self.config.get("path", ""))
        if not root.exists():
            return False, f"path does not exist: {root}"
        count = sum(1 for _ in root.rglob("*.json"))
        if count == 0:
            return False, f"no documents under {root}"
        return True, f"{count} document(s)"


class S3PrefixConnector(SourceConnector):
    """Documents already in S3 -- policy PDFs, exported wiki pages, prior
    questionnaires. The general-purpose path for anything that can be dropped
    in a bucket, and the recommended way to onboard a new repository quickly
    without writing a bespoke connector."""

    type_name = "s3_prefix"

    def discover(self) -> Iterator[SourceDocument]:
        import boto3  # deferred: keeps local tests free of an AWS dependency

        s3 = boto3.client("s3")
        bucket = self.config["bucket"]
        prefix = self.config.get("prefix", "")
        suffixes = tuple(self.config.get("suffixes", [".md", ".txt", ".json"]))

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(suffixes) or key.endswith(".metadata.json"):
                    continue
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                yield SourceDocument(
                    document_id=self._stable_id(bucket, key),
                    content=body.decode("utf-8", errors="replace"),
                    source_type=self.source_type,
                    source_id=self.source_id,
                    updated_at=obj["LastModified"].isoformat(),
                    extra_metadata={"s3_key": key},
                )

    def health_check(self) -> tuple[bool, str]:
        import boto3

        try:
            boto3.client("s3").head_bucket(Bucket=self.config["bucket"])
            return True, "bucket reachable"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)


# ---------------------------------------------------------------------------
# Placeholders -- wired into config and the registry, not yet implemented.
#
# Each raises NotImplementedError with the specific decisions its
# implementation needs. That is deliberate: an empty stub invites someone to
# fill it in without answering the auth and permission questions, which are
# the hard part of every one of these.
# ---------------------------------------------------------------------------


class ConfluenceConnector(SourceConnector):
    """Confluence spaces holding security policy and control documentation."""

    type_name = "confluence"

    def discover(self) -> Iterator[SourceDocument]:
        raise NotImplementedError(
            "Confluence connector not implemented. Decisions required first:\n"
            "  1. Auth: service account (2LO) or per-user OBO via AgentCore\n"
            "     Identity? Per-user preserves space-level ACLs; a service\n"
            "     account flattens them and will over-share if any space is\n"
            "     restricted.\n"
            "  2. Scope: which space keys, and are archived pages excluded?\n"
            "     Superseded policy is the main source of stale answers.\n"
            "  3. Path: 'gate' (pull via API, redact, push to sanitised bucket)\n"
            "     or 'native' (Bedrock KB Confluence connector + POST_CHUNKING).\n"
            "     Native cannot honour the pre-ingest gate -- see module docstring."
        )


class SharePointConnector(SourceConnector):
    """SharePoint document libraries -- typically where signed policy PDFs and
    completed questionnaire workbooks live."""

    type_name = "sharepoint"

    def discover(self) -> Iterator[SourceDocument]:
        raise NotImplementedError(
            "SharePoint connector not implemented. Decisions required first:\n"
            "  1. Entra ID app registration with Sites.Selected, scoped to named\n"
            "     sites -- not Sites.Read.All, which grants the tenant.\n"
            "  2. Whether document-library permissions must be preserved into\n"
            "     retrieval, or whether the corpus is uniformly readable by\n"
            "     anyone who can use the tool. This is a policy question, not\n"
            "     a technical one, and it should be answered explicitly.\n"
            "  3. Version handling: index current version only, or retain\n"
            "     superseded versions with validity windows for audit trail?"
        )


class TrustPortalConnector(SourceConnector):
    """Published trust-centre content -- already externally visible, so the
    lightest redaction profile applies. Useful as a consistency check: an
    answer that contradicts the public trust portal is a finding in itself."""

    type_name = "trust_portal"

    def discover(self) -> Iterator[SourceDocument]:
        raise NotImplementedError(
            "Trust portal connector not implemented. Decisions required first:\n"
            "  1. Source: published page crawl, or an export/API if the portal\n"
            "     vendor offers one? Crawling renders JS-heavy portals poorly.\n"
            "  2. Whether contradiction detection against this source should\n"
            "     demote an answer's tier -- recommended, since a public\n"
            "     contradiction is the highest-consequence failure mode."
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, type[SourceConnector]] = {
    cls.type_name: cls
    for cls in (
        GoldenQuestionnaireConnector,
        S3PrefixConnector,
        ConfluenceConnector,
        SharePointConnector,
        TrustPortalConnector,
    )
}


def build_connector(source_id: str, config: dict) -> SourceConnector:
    type_name = config.get("type")
    if type_name not in REGISTRY:
        raise ValueError(
            f"unknown source type {type_name!r} for source {source_id!r}. "
            f"Known types: {', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[type_name](source_id, config)
