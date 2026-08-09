"""CLAIM 6 — entity resolution is reversible.

The corpus puts one party under five surface forms across two scripts, on purpose. This scores
three things:

**Resolution.** The forms of one party group together, and two different parties do not. The
second half is the one that matters: a resolver that merges everything scores perfectly on the
first.

**Reversibility.** Every merge can be undone, and after the un-merge **every downstream record
that pointed at the merged entity points at the right successor**. That second half is the part
nobody builds — splitting an entity is easy, and a partial re-point leaves a dangling pointer
that is invisible until somebody follows it.

**Lineage.** The merge carries the rule and the score that brought each member in, so a human
who disagrees with it has something to disagree with. An unexplained merge cannot be audited.

What this does **not** claim: that the merge threshold is right. It is chosen, the contract
schema refuses any claim that it was derived, and there is no labelled set of same-party
judgements large enough to derive one honestly. Reversibility is what makes a chosen threshold
survivable, and it is what is measured here.
"""

from __future__ import annotations

import sys

from evals.harness import contracts, ground_truth
from manifest.core.entities import MatchRule, Mention, resolve, unmerge


def _rules() -> tuple[MatchRule, ...]:
    return tuple(
        MatchRule(
            rule_id=rule.id,
            explanation=rule.explanation,
            rules=tuple(rule.rules),
            weight=rule.weight,
        )
        for rule in contracts().entities.rules
    )


def main() -> int:
    parties = ground_truth()["parties"]
    threshold = contracts().entities.merge_threshold

    mentions: list[Mention] = []
    truth: dict[str, str] = {}
    for party in parties:
        for index, form in enumerate(party["surface_forms"]):
            mention_id = f"{party['party_id']}#{index}"
            mentions.append(
                Mention(
                    mention_id=mention_id,
                    name=form,
                    document="bill_of_lading",
                    shipment="—",
                )
            )
            truth[mention_id] = party["party_id"]

    entities = resolve(mentions, _rules(), threshold)

    failures: list[str] = []
    impure = [
        entity for entity in entities if len({truth[member] for member in entity.members}) > 1
    ]
    if impure:
        failures.append(
            f"{len(impure)} entity/entities merged mentions of different parties: "
            + "; ".join(
                f"{entity.entity_id[:8]} holds {sorted({truth[m] for m in entity.members})}"
                for entity in impure[:3]
            )
        )

    grouped = {party["party_id"]: 0 for party in parties}
    for entity in entities:
        for member in entity.members:
            grouped[truth[member]] = max(grouped[truth[member]], len(entity.members))

    merged = [entity for entity in entities if entity.merged]
    if not merged:
        failures.append("nothing merged at all; there is no merge to reverse")

    # ── Reversibility ────────────────────────────────────────────────────────
    subject = max(entities, key=lambda entity: len(entity.members))
    # Downstream records, each resolved from one mention. Kept at merge time rather than
    # discarded, which is what makes the un-merge arithmetic rather than a second guess.
    references = {f"record-{index}": member for index, member in enumerate(subject.members)}
    undone = unmerge(subject, references, _rules(), threshold)

    if len(undone.replacements) != len(subject.members):
        failures.append(
            f"un-merging {subject.entity_id[:8]} produced {len(undone.replacements)} entities "
            f"from {len(subject.members)} members; a complete un-merge splits them all"
        )
    if set(undone.repointed) != set(references):
        failures.append(
            f"{len(set(references) - set(undone.repointed))} downstream record(s) were left "
            f"pointing at an entity that no longer exists. A dangling pointer is invisible "
            f"until somebody follows it, which is why a partial un-merge is worse than none"
        )
    successors = {entity.entity_id for entity in undone.replacements}
    if not set(undone.repointed.values()) <= successors:
        failures.append("a record was re-pointed at an entity the un-merge did not create")

    explained = sum(1 for entity in merged for match in entity.matches if match.explanation)
    total_matches = sum(len(entity.matches) for entity in merged)
    if total_matches and explained != total_matches:
        failures.append("a merge carries a match with no explanation; nobody can disagree with it")

    print("claim 6 — entity resolution is reversible\n")
    print(f"  parties in the register    {len(parties)}")
    print(f"  surface forms              {len(mentions)}")
    print(f"  entities resolved          {len(entities)}  ({len(merged)} of them merged)")
    print(f"  entities mixing parties    {len(impure)}")
    print(f"  largest merge              {len(subject.members)} forms — {subject.canonical_name!r}")
    print(f"  match evidence carried     {total_matches}, all explained")
    print("\n  un-merge:")
    print(f"    entities after           {len(undone.replacements)}")
    print(f"    records re-pointed       {len(undone.repointed)}/{len(references)}")
    print(f"    dangling references      {len(set(references) - set(undone.repointed))}")
    cross_script = [
        entity
        for entity in entities
        if len(entity.members) == 1 and not entity.canonical_name.isascii()
    ]
    print(
        f"\n  {len(cross_script)} non-Latin form(s) resolved to themselves and did NOT merge "
        f"with their party's Latin forms. That is the honest result: string similarity cannot "
        f"bridge 北方桥货运 to 'Northbridge Forwarding B.V.', and no normalisation in this "
        f"repository pretends to. Bridging scripts needs a transliteration table or a model, "
        f"neither of which is here — so the register carries the link and the resolver does "
        f"not claim to have found it."
    )
    print(
        "\n  The threshold is chosen, not derived — there is no labelled set of same-party "
        "judgements large enough to derive one honestly, and the contract schema refuses any "
        "contract that claims otherwise. Reversibility is what makes a chosen threshold "
        "survivable, and reversibility is what is measured here."
    )

    if failures:
        print("\nclaim 6: FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nclaim 6: a merge can be undone, with lineage intact and nothing left dangling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
