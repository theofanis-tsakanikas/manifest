"""Resolve party names across documents, and un-merge when a merge turns out to be wrong.

**Claim 6, on the estate.** `evals/entities/` has proved reversibility offline since the module
was written; nothing in the running system had ever resolved two mentions or undone a merge. The
claim was a property of a pure function and an untested property of the estate.

**It decides nothing.** `core.entities.resolve` groups mentions and `core.entities.unmerge` undoes
a grouping; the matching rules, their normalisations and the threshold are data in
`contracts/entities/parties.yaml`. This handler reads records, builds `Mention`s from the fields
the contracts declare as parties, and stores what those functions return.

**The references are stored at merge time, and that is the whole of the reversibility.**
`unmerge` takes a map from each downstream record to the *mention* it resolved from — kept rather
than discarded — which makes un-merging arithmetic instead of guesswork. A design that stored
only the entity id would have to re-run resolution to undo a merge, and re-running the thing that
made the mistake is not a correction. So the state written here always carries mentions and
references, never just the entities.

**Only published party values are mentions.** A name that abstained is a reading nobody approved,
and merging two parties on the strength of it would attach a shipment to a company on evidence
the system itself refused to publish.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from manifest.core.entities import MatchRule, Mention, resolve, unmerge

#: The declared field type whose values are party names. Everything else on a document — a port,
#: a vessel, a container — is a string that happens to look like one.
PARTY_TYPE = "party"

#: Where the resolved state lives. One object per shipment, holding the entities *and* the
#: mentions and references they were built from, because the last two are what makes a merge
#: reversible.
PREFIX = "entities"


class HandlerError(RuntimeError):
    """Refusal."""


def _client(name: str) -> Any:
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        name, config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})
    )


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HandlerError(f"{name} is not set; refused rather than defaulted")
    return value


def _contracts() -> Any:
    from manifest.contracts.loader import load  # noqa: PLC0415

    return load(Path(os.environ.get("CONTRACTS_DIR", "/var/task/contracts")))


def _rules(contracts: Any) -> tuple[MatchRule, ...]:
    """The contract's match rules as `core` wants them.

    The same construction `evals/entities` performs, because both drive the same function — and
    a handler that built them differently would be resolving by rules the offline claim never
    scored, which is the shape of a claim that is true in CI and false in the estate.
    """
    return tuple(
        MatchRule(
            rule_id=rule.id,
            explanation=rule.explanation,
            rules=tuple(rule.rules),
            weight=rule.weight,
        )
        for rule in contracts.entities.rules
    )


def _read(bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return json.loads(_client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
    # Broad on purpose: a missing key and a denied read are the same answer to the caller.
    except Exception:
        return None


def _write(bucket: str, key: str, body: dict[str, Any]) -> None:
    _client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, indent=1).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=_env("DATA_KEY_ARN"),
    )


def _mentions(bucket: str, shipment: str, documents: list[dict[str, Any]]) -> list[Mention]:
    """Every published party name on these documents, as a mention.

    The mention id names the document and the field it came from, so it is stable across a
    re-resolution and legible in a reference map — an opaque counter would make the stored
    references unreadable exactly when somebody is trying to undo a merge.
    """
    contracts = _contracts()
    found: list[Mention] = []
    for named in documents:
        document_id, version = str(named.get("document_id", "")), str(named.get("version", ""))
        if not document_id or not version:
            raise HandlerError("each document needs a document_id and a version")
        record = _read(bucket, f"records/{document_id}/{version}.json")
        if record is None:
            raise HandlerError(f"no published record for {document_id} at {version}")

        contract = contracts.document(str(record.get("document_type") or ""))
        for entry in record.get("fields", []):
            if not (entry.get("publishable") and entry.get("value")):
                continue
            try:
                declared = contract.field(str(entry["field"]))
            except KeyError as error:
                # A published record carrying a field its own contract does not declare is an
                # inconsistency, not something to skip past: the record was produced against a
                # contract, and if the two disagree the question is which one is wrong. Refused
                # by name rather than raised as a bare `KeyError` from a lookup three frames in.
                raise HandlerError(
                    f"{document_id} published {entry['field']!r} and "
                    f"contracts/documents/{record.get('document_type')}.yaml does not declare "
                    f"it. The record and the contract disagree about what this document has"
                ) from error
            if declared.type.value != PARTY_TYPE:
                continue
            found.append(
                Mention(
                    mention_id=f"{document_id}#{entry['field']}",
                    name=str(entry["value"]),
                    document=document_id,
                    shipment=shipment,
                )
            )
    return found


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """`{action: resolve|unmerge, ...}`."""
    action = str(event.get("action") or "")
    if action == "resolve":
        return _resolve(event)
    if action == "unmerge":
        return _unmerge(event)
    raise HandlerError(
        f"{action!r} is not an action. `resolve` groups mentions into parties; `unmerge` undoes "
        f"one grouping and re-points what referenced it. There is no third thing"
    )


def _resolve(event: dict[str, Any]) -> dict[str, Any]:
    shipment = str(event.get("shipment") or "")
    documents = event.get("documents")
    if not shipment or not isinstance(documents, list) or not documents:
        raise HandlerError("give {'shipment': '<id>', 'documents': [{document_id, version}, ...]}")

    bucket = _env("RECORDS_BUCKET")
    contracts = _contracts()
    mentions = _mentions(bucket, shipment, documents)
    entities = resolve(mentions, _rules(contracts), contracts.entities.merge_threshold)

    # The reference map, written at merge time because that is what makes the merge reversible.
    # One entry per mention: the downstream record that used it, and the mention it used.
    references = {mention.mention_id: mention.mention_id for mention in mentions}
    state = {
        "shipment": shipment,
        "mentions": [
            {"mention_id": m.mention_id, "name": m.name, "document": m.document} for m in mentions
        ],
        "references": references,
        "entities": [_as_json(entity) for entity in entities],
    }
    _write(bucket, f"{PREFIX}/{shipment}/current.json", state)

    return {
        "shipment": shipment,
        "mentions": len(mentions),
        "entities": len(entities),
        "merged": sum(1 for entity in entities if entity.merged),
        "resolved": [_as_json(entity) for entity in entities],
    }


def _unmerge(event: dict[str, Any]) -> dict[str, Any]:
    shipment, entity_id = str(event.get("shipment") or ""), str(event.get("entity_id") or "")
    if not shipment or not entity_id:
        raise HandlerError("give {'shipment': '<id>', 'entity_id': '<id>'}")

    bucket = _env("RECORDS_BUCKET")
    state = _read(bucket, f"{PREFIX}/{shipment}/current.json")
    if state is None:
        raise HandlerError(
            f"nothing has been resolved for {shipment}. An un-merge undoes a merge this system "
            f"made, and there is no record of one"
        )

    stored = next((e for e in state["entities"] if e["entity_id"] == entity_id), None)
    if stored is None:
        raise HandlerError(f"{shipment} has no entity {entity_id}")

    from manifest.core.entities import Entity  # noqa: PLC0415

    contracts = _contracts()
    undone = unmerge(
        Entity(
            entity_id=stored["entity_id"],
            canonical_name=stored["canonical_name"],
            members=tuple(stored["members"]),
        ),
        # **Only the references that point into this entity.** `core.entities.unmerge` refuses
        # a map containing anything else, and it is right to: a reference to a mention of some
        # *other* party has no successor among these replacements, so re-pointing it would be a
        # guess and leaving it would be the dangling pointer the whole function exists to
        # prevent. Passing the shipment's whole map was this handler's bug and that guard found
        # it — which is the second time today a pure function has caught its own adapter.
        {
            reference: mention
            for reference, mention in state["references"].items()
            if mention in stored["members"]
        },
        _rules(contracts),
        contracts.entities.merge_threshold,
        keep_together=tuple(tuple(group) for group in event.get("keep_together", ())),
    )

    remaining = [e for e in state["entities"] if e["entity_id"] != entity_id]
    remaining.extend(_as_json(entity) for entity in undone.replacements)
    # **The lineage stays.** The removed entity is recorded rather than deleted: doctrine rule 4
    # applied to a merge, so "what did this system think last week" has an answer.
    state["entities"] = remaining
    state.setdefault("unmerged", []).append(
        {"removed": undone.removed, "into": [e.entity_id for e in undone.replacements]}
    )
    _write(bucket, f"{PREFIX}/{shipment}/current.json", state)

    return {
        "shipment": shipment,
        "removed": undone.removed,
        "replacements": [_as_json(entity) for entity in undone.replacements],
        # The half nobody builds. A partial answer here leaves a dangling pointer that is
        # invisible until somebody follows it.
        "repointed": undone.repointed,
    }


def _as_json(entity: Any) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "canonical_name": entity.canonical_name,
        "members": list(entity.members),
        "merged": entity.merged,
        "matches": [
            {"left": m.left, "right": m.right, "score": str(m.score), "rule": m.rule_id}
            for m in entity.matches
        ],
    }
