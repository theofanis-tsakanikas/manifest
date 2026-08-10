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

import pytest

from manifest.core.fields import Extracted
from manifest.core.review import Reason
from manifest.handlers import provenance_gate, publish, read_tier0


class TestTheReaderRefusesWhatItCannotSafelyAssume:
    def test_an_event_missing_its_required_keys_is_refused(self) -> None:
        with pytest.raises(read_tier0.HandlerError, match="missing"):
            read_tier0.Request.of({})

    @pytest.mark.parametrize("key", ["../../etc/passwd", "/absolute", "a/../../b"])
    def test_an_object_key_that_traverses_is_refused(self, key: str) -> None:
        """The key comes from an S3 notification, so a counterparty chose it.

        It becomes a local path when the object is downloaded, which is the whole of the
        problem: everything else in this system treats counterparty content as data, and an
        object key is counterparty content.
        """
        with pytest.raises(read_tier0.HandlerError, match="traverses"):
            read_tier0.Request.of({"bucket": "b", "key": key, "document_id": "d", "language": "en"})

    @pytest.mark.parametrize("language", [None, "", "   "])
    def test_a_missing_language_is_refused_rather_than_defaulted_to_english(
        self, language: str | None
    ) -> None:
        """The most consequential default this handler could have.

        A Greek page read as English does not fail. It returns confident text in the wrong
        alphabet, that confidence enters a threshold derived on the assumption that it means
        something, and `contracts/cascade/` routes on the language it was told.
        """
        with pytest.raises(read_tier0.HandlerError, match="no default"):
            read_tier0.Request.of(
                {"bucket": "b", "key": "k", "document_id": "d", "language": language}
            )

    def test_a_complete_event_parses(self) -> None:
        request = read_tier0.Request.of(
            {"bucket": "landing", "key": "in/SHP1.pdf", "document_id": "SHP1/bol", "language": "el"}
        )
        assert (request.bucket, request.language) == ("landing", "el")

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
