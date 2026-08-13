"""The handlers refuse what they should, offline, with no AWS anything.

These are the three functions that will run in the estate, and they are tested here the same way
everything else in this repository is: on a laptop, with no credentials and no SDK installed. The
cloud clients are constructed inside the functions that use them precisely so that this file can
exercise parsing, refusals and payload shape without any of that.

What is asserted is almost entirely **refusal**. A handler's happy path is a few lines of
plumbing between `core` functions that have their own tests; what is worth pinning down is that
it does not default, does not guess, and does not let a failure through as a pass.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from manifest.core.fields import Extracted
from manifest.core.review import Reason
from manifest.handlers import escalate, provenance_gate, publish, read_tier0
from manifest.handlers.escalate import SCORING_TIERS


class TestTheReaderRefusesWhatItCannotSafelyAssume:
    def test_an_event_missing_its_required_keys_is_refused(self) -> None:
        with pytest.raises(read_tier0.HandlerError, match="bucket and a key"):
            read_tier0.Request.of({})

    @pytest.mark.parametrize("key", ["../../etc/passwd", "/absolute", "a/../../b"])
    def test_an_object_key_that_traverses_is_refused(self, key: str) -> None:
        """The key comes from an S3 notification, so a counterparty chose it.

        It becomes a local path when the object is downloaded, which is the whole of the
        problem: everything else in this system treats counterparty content as data, and an
        object key is counterparty content.
        """
        with pytest.raises(read_tier0.HandlerError, match="traverses"):
            read_tier0.Request.of({"bucket": "b", "key": key})

    @pytest.mark.parametrize(
        "key",
        [
            "incoming/SHP1.pdf",
            "incoming/el/SHP1.pdf",
            "incoming/greek/bill_of_lading/SHP1.pdf",
            "incoming/el/bill_of_lading/SHP1.tiff",
            "elsewhere/el/bill_of_lading/SHP1.pdf",
        ],
    )
    def test_a_key_outside_the_convention_is_refused_rather_than_guessed(self, key: str) -> None:
        """The language and the document type arrive **only** through the key.

        There is no default for either, and this is the most consequential pair of defaults this
        handler could have had. A Greek page read as English does not fail — it returns confident
        text in the wrong alphabet, and that confidence then enters a threshold derived on the
        assumption that it means something. A document thresholded against the wrong contract is
        the same failure from the other side.
        """
        with pytest.raises(read_tier0.HandlerError, match="landing convention"):
            read_tier0.Request.of({"bucket": "b", "key": key})

    def test_a_key_in_the_convention_yields_the_language_and_the_type(self) -> None:
        request = read_tier0.Request.of(
            {"bucket": "landing", "key": "incoming/el/bill_of_lading/SHP00001.pdf"}
        )
        assert request.language == "el"
        assert request.document_type == "bill_of_lading"
        assert request.document_id == "SHP00001"

    def test_the_required_language_set_covers_the_two_no_managed_service_reads(self) -> None:
        """Greek and Dutch are the scenario's whole argument for a local tier 0."""
        assert {"ell", "nld"} <= read_tier0.REQUIRED_LANGUAGES


class TestPublishRefusesToGuessTheContract:
    def test_an_event_without_a_reading_pointer_is_refused(self) -> None:
        with pytest.raises(publish.HandlerError, match="reading"):
            publish.handler({"document_type": "bill_of_lading"})

    def test_a_missing_document_type_is_refused_rather_than_inferred(self) -> None:
        """The contract decides which fields exist and what each one's error budget is.

        Inferring it from the key would apply one document's rules to another's page, and the
        failure would look like a document that simply had no fields.
        """
        with pytest.raises(publish.HandlerError, match="no default"):
            publish.handler({"reading": {"bucket": "b", "key": "k"}})

    def test_a_field_absent_from_the_threshold_artefact_is_refused_not_always_reviewed(
        self,
    ) -> None:
        """The subtle one, and the reason `_outcome` checks membership rather than `.get`.

        `thresholds.get(field)` returns `None` both for a field declared always-review and for
        a field the derivation never produced. Treating the second as the first would hide a
        deployment that does not match its contract behind behaviour that looks deliberate.
        """
        found = Extracted(
            field="gross_weight",
            value="27000",
            confidence=0.98,
            page=1,
            box=None,
            anchor_similarity=1.0,
            reason="found",
        )
        with pytest.raises(publish.HandlerError, match="does not match its contract"):
            publish._outcome("gross_weight", found, thresholds={"other_field": 0.9})

    def test_an_explicit_none_in_the_artefact_means_always_review(self) -> None:
        found = Extracted(
            field="hs_code",
            value="8802.40",
            confidence=0.999,
            page=1,
            box=None,
            anchor_similarity=1.0,
            reason="found",
        )
        outcome = publish._outcome("hs_code", found, thresholds={"hs_code": None})
        assert outcome.queued_because is Reason.ALWAYS_REVIEW
        assert not outcome.publishable, "0.999 does not publish an always-review field"

    def test_an_unscored_value_is_queued_as_unscored_at_any_threshold(self) -> None:
        found = Extracted(
            field="gross_weight",
            value="27000",
            confidence=None,
            page=1,
            box=None,
            anchor_similarity=1.0,
            reason="found",
        )
        outcome = publish._outcome("gross_weight", found, thresholds={"gross_weight": 0.0})
        assert outcome.queued_because is Reason.UNSCORED
        assert not outcome.publishable


class TestTheGateFailsClosed:
    def test_a_queued_field_is_not_counted_as_a_refusal(self) -> None:
        """The bug that stopped the deployed estate publishing anything at all.

        A field already bound for the queue is not checked — `_check` marks it
        `not_applicable`. The refusal count was `verdict != "verified"`, which swept those in,
        so any document with a single abstaining field came out `verified: false` and the state
        machine took the queue branch. With 31 of 36 fields declared always-review, that is
        every document.

        It was invisible from inside: the execution succeeded, the queue received the document,
        and the record was simply absent — which is exactly what a fully-abstaining document is
        supposed to look like. The first bill of lading through the deployed pipeline had two
        fields clear their thresholds and seven abstain, both published fields verified against
        the page, and the run still ended in the queue.
        """
        checked = [
            {"field": "gross_weight", "verdict": "verified"},
            {"field": "bill_of_lading_number", "verdict": "not_applicable"},
            {"field": "consignee", "verdict": "not_applicable"},
        ]
        refused = [
            entry for entry in checked if entry["verdict"] not in ("verified", "not_applicable")
        ]
        assert not refused, "a queued field is not a refused field"

    def test_an_uncheckable_field_is_still_a_refusal(self) -> None:
        """`uncheckable` is not `not_applicable`, and merging them would be the laundering.

        A field nothing could look at has not been verified. Publishing on the strength of "we
        could not check" is precisely what the verdict vocabulary exists to prevent, so only
        "we did not check this, because it was never going to publish" is excluded.
        """
        checked = [
            {"field": "gross_weight", "verdict": "verified"},
            {"field": "vessel_name", "verdict": "uncheckable"},
        ]
        refused = [
            entry for entry in checked if entry["verdict"] not in ("verified", "not_applicable")
        ]
        assert [entry["field"] for entry in refused] == ["vessel_name"]

    def test_an_event_without_fields_is_refused(self) -> None:
        with pytest.raises(provenance_gate.HandlerError, match="fields"):
            provenance_gate.handler({"document_id": "d", "document_type": "bill_of_lading"})

    def test_a_missing_language_is_refused(self) -> None:
        """The re-read layer reads the crop in a language.

        Reading a Greek crop as English returns confident text in the wrong alphabet, which
        refuses a *correct* field — and a gate that manufactures failures is a gate somebody
        mutes within a week.
        """
        with pytest.raises(provenance_gate.HandlerError, match="no language"):
            provenance_gate.handler(
                {"document_id": "d", "document_type": "bill_of_lading", "fields": []}
            )

    def test_an_unexpected_error_refuses_the_field_rather_than_passing_it(self) -> None:
        """The catastrophic edit this file exists to prevent.

        `except Exception: return verified` would turn every failure of the check into a
        passing check, and the pipeline would keep reporting green while publishing fields
        nobody looked at.
        """
        entry = {
            "field": "gross_weight",
            "value": "27000",
            "page": 1,
            "box": None,
            "publishable": True,
        }

        class _Contract:
            def field(self, name: str) -> object:
                raise RuntimeError("the contract could not be read")

        result = provenance_gate._check(entry, _Contract(), "en", raster=None)
        assert result["verdict"] == "refused"
        assert "not a pass" in result["check_reason"]

    def test_a_queued_field_is_marked_not_applicable_rather_than_verified(self) -> None:
        """Marking it verified would be a lie an aggregate over this list would then count."""
        entry = {"field": "hs_code", "publishable": False}
        result = provenance_gate._check(entry, contract=None, language="en", raster=None)
        assert result["verdict"] == "not_applicable"

    def test_a_publishable_field_with_no_box_is_refused(self) -> None:
        """Doctrine rule 7 at the pipeline's own boundary: no provenance, no publication."""
        entry = {
            "field": "gross_weight",
            "value": "27000",
            "page": 1,
            "box": None,
            "publishable": True,
        }

        class _Contract:
            def field(self, name: str) -> object:
                class _Field:
                    comparison = ()

                    class type:
                        value = "text"

                return _Field()

        result = provenance_gate._check(entry, _Contract(), "en", raster=None)
        assert result["verdict"] == "refused"


class TestEscalation:
    """The cascade's own rules, asserted where a mutation can reach them."""

    def test_only_the_scoring_tiers_may_publish(self) -> None:
        """The fact that decides the cascade's economics, kept in one place.

        Tiers 0 and 1 report a confidence per value; tiers 2 and 3 report none anywhere. A page
        routed above tier 1 therefore comes back better read and with no score to publish on —
        it reaches a human. If this set ever grew to include 2 or 3, a field would publish on a
        number nothing measured, which is the fabricated result the whole project argues against.
        """
        assert frozenset({0, 1}) == SCORING_TIERS

    def test_the_contract_agrees_about_which_tiers_may_publish(self) -> None:
        """The handler's constant and the contract's declaration must say the same thing.

        **This test used to read the contract's prose, and it passed for the wrong reason.** It
        grepped each tier's description for "no confidence" and required the phrase to appear
        exactly where `SCORING_TIERS` excluded the tier. Then tier 2's description was corrected
        — the service *does* report a per-word confidence, 0.729 to 1.0, contradicting the
        published schema the description cited — and the correction **quotes the old sentence**.
        The grep matched the quotation, the test stayed green, and the fact it was protecting had
        reversed.

        A paragraph about a phrase satisfies a search for that phrase. So the contract states it
        as a field now, and this reads the field.
        """
        root = pathlib.Path(__file__).resolve().parents[2]
        contract = yaml.safe_load((root / "contracts/cascade/routing.yaml").read_text())
        declared = {
            int(tier)
            for tier, publishes in contract["publishes_on_its_own_score"].items()
            if publishes
        }

        assert declared == SCORING_TIERS, (
            f"the contract says tiers {sorted(declared)} may publish on their own score and the "
            f"handler says {sorted(SCORING_TIERS)}. One of them is deciding whether a value "
            f"reaches a customer on a number nothing in this repository has calibrated"
        )

    def test_it_escalates_the_pages_the_fields_are_on(self) -> None:
        """Not page one, which is what the first version of this handler read.

        255 of this corpus's 3,000 documents run to a second page. A field on page two
        escalated against `page-0001.png` comes back empty — and empty is indistinguishable
        from a tier that could not read it either, so the failure is both expensive and silent.

        Asserted on the source rather than by invoking the handler, because reaching the call
        needs S3, Textract and a threshold artefact. What is being protected is one decision:
        which page numbers become object keys.
        """
        raw = (
            pathlib.Path(__file__).resolve().parents[2] / "src/manifest/handlers/escalate.py"
        ).read_text(encoding="utf-8")
        # **Comments stripped before searching.** The first version of this test failed on the
        # handler's own comment, which names `page-0001.png` while explaining why it is wrong.
        # A check that reads prose as though it were code is the same defect that once had
        # `check_deploy_path` matching the *word* "default" in a description — and it fails in
        # the direction that looks like rigour.
        source = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
        assert "page-{number:04d}.png" in source, "the key must be built from a page number"
        assert "page-0001.png" not in source, (
            "a hard-coded first page ignores every field on page two, silently"
        )
        assert 'entry["page"]' in source, "the pages must come from the fields that abstained"

    def test_an_event_without_an_outcome_is_refused(self) -> None:
        with pytest.raises(escalate.HandlerError, match="outcome"):
            escalate.handler({})

    def test_a_missing_language_is_refused_rather_than_guessed(self) -> None:
        """Language decides which tiers may read the page at all — there is no default."""
        with pytest.raises(escalate.HandlerError, match="language"):
            escalate.handler(
                {
                    "extraction": {
                        "outcome": {
                            "document_id": "d",
                            "document_type": "bill_of_lading",
                            "fields": [],
                        }
                    }
                }
            )
