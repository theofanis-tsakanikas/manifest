# The tier-0 reader, as an image.
#
# **The reader is the artefact, not the code around it.** Every threshold in this repository is
# derived from a committed recording produced by this binary at this version with this language
# data. A deployed tier 0 running a different build would produce different confidences, and
# every claim-1 figure would become a statement about a machine nobody can reproduce. So the
# version is asserted at build time, and the image is what both the laptop recording ceremony
# and the estate use.
#
# It carries `read_tier0` and `provenance_gate`, because both need the raster and the reader.
# `publish` runs on plain Python and ships as a zip; keeping it out means a change to the
# extraction logic does not rebuild a large image.
#
# **Debian, not the AWS Lambda base image.** The first version started from
# `public.ecr.aws/lambda/python:3.12` and failed on the first real build: *"No package matches
# 'tesseract'"*. Amazon Linux 2023 does not carry the reader or its language data in its default
# repositories, and reaching it needs a third-party repository whose contents nobody here
# controls — for a binary whose exact version every threshold in this project depends on.
#
# Debian carries `tesseract-ocr` and its language packs as first-class packages, so the reader
# and the Greek and Dutch data come from the same distribution that pins them. The Lambda
# runtime interface is a pip package (`awslambdaric`), which is the documented way to run a
# container image that is not built from an AWS base.
#
# **The price of leaving the AWS base is that its batteries leave with it.** An AWS base image
# ships `boto3`; this one does not, and the handlers import it. The first document through the
# deployed pipeline failed with `No module named 'boto3'` — after the image built, after the
# functions were created, after the trigger fired. Nothing before the first real execution could
# have said so, because the module is imported inside the function that uses it precisely so the
# unit tests run on a machine with no AWS libraries at all.
#
# That was the right call for the tests and it is exactly what hid this: a lazy import moves the
# failure from load time to call time, which is later and quieter.

# **Trixie, not bookworm, and the reason is the whole point of the assertion below.**
#
# The first build from `bookworm` was refused by that assertion: *"reader is 5.3.0, this image
# expects 5.5.x"*. Every threshold in `recordings/` was derived from the 5.5 series, and 5.3
# would have produced different confidences on the same pages — quietly, with nothing
# downstream noticing a distribution shifting by two points. The build failing is the ceremony
# in `make ocr-record` enforced at the other end, and it worked on the first real deploy.
#
# Trixie carries the 5.5 series. If a future base moves it again, this build fails again, by
# name, and the answer is to re-record and accept the movement — never to widen the assertion.
FROM public.ecr.aws/docker/library/python:3.12-slim-trixie

# **What the series check let through, and what it cost.**
#
# This assertion used to compare the *series* — `5.5` — on the argument that a patch release
# does not move confidences and that pinning exactly would make the image unbuildable the week
# a security fix ships. Both halves of that argument were wrong in the way that matters.
#
# The recording was made on the author's laptop by Homebrew's `tesseract 5.5.2`. This image
# carries Debian's `5.5.0`. The series check passed, the deploy went green, and the first
# document to reach the pipeline failed on a threshold artefact that could not be found —
# because the artefact is keyed by the *exact* reader identity and the estate's reader was not
# the recording's reader. That is the failure working as designed, arriving three layers late
# and wearing an IAM error's clothes.
#
# And the "just pick a better base" answer does not exist: no Linux distribution ships 5.5.2.
# Not trixie, not sid, not any Ubuntu including the unreleased one. The version every threshold
# here was derived from is one the estate can never run.
#
# So the assertion is exact, and the recording moved to where the binary is:
# `.github/workflows/record.yml` runs the ceremony **inside this image**. The string below is
# the recording's own `reader_version`, verbatim, and `scripts/reader_version_check.py` fails
# CI while the two disagree. A distribution that moves the reader now fails this build by name,
# and the answer is to re-record through the ceremony and accept the movement — which is a
# thing this repository can now actually do.

# The reader and its language data. Greek and Dutch are the two the scenario turns on and the
# two no managed service reads — `docs/AWS-CONSTRAINTS.md` — so their absence is not a
# degradation, it is the loss of the only reader that can see those pages at all.
#
# Named individually rather than pulled as a group: an image silently missing one language would
# read those pages in the wrong script and return confident nonsense.
#
# **`fonts-noto-cjk` is here for the same reason the reader is, and it was found the same way.**
#
# `corpus/sheet.py` used to take the first font it found on the machine. On the author's laptop
# that is Arial Unicode; on a Linux runner it was DejaVu — which covers Latin and Greek and
# **not CJK**, so the thirteen Chinese characters in the corpus's party names rendered as empty
# boxes. The corpus generated on a runner was not merely a different corpus, it was a worse one,
# and every check stayed green.
#
# Box geometry comes from font metrics, so the font is part of the ground truth exactly as the
# reader's version is part of the recording. Noto Sans CJK covers all three scripts in one file,
# the distribution pins it, and `sheet.py` will now accept nothing else — a corpus that cannot
# be generated outside this image is a corpus whose boxes mean one thing everywhere.
#
# It costs roughly 200 MB of image, paid by a function that never renders a page. That is the
# price of the corpus and the reading coming from the same machine, and it is cheaper than a
# claim scored against tofu.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      tesseract-ocr-ell \
      tesseract-ocr-nld \
      tesseract-ocr-deu \
      tesseract-ocr-fra \
      poppler-utils \
      libgl1 \
      libglib2.0-0 \
      fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# **The version and the languages, asserted rather than hoped for.**
#
# A base image is rebuilt by its publisher and a rebuild can move a package. If that moved the
# reader, every derived threshold would drift on the next deploy — silently, because nothing
# downstream notices a confidence distribution shifting by two points. `make ocr-record` refuses
# to overwrite a recording without an accepted movement report for exactly this reason; this is
# the same rule enforced at the other end.
#
# `EXPECTED_READER_VERSION` is the recording's `reader_version` field, character for character,
# so that the comparison needs no parsing on either side and the two cannot drift through a
# format. `scripts/reader_version_check.py` proves they still match, in CI, on every push.
ARG EXPECTED_READER_VERSION="tesseract 5.5.0"
RUN installed="tesseract $(tesseract --version 2>&1 | head -1 | awk '{print $2}')" \
    && if [ "$installed" != "$EXPECTED_READER_VERSION" ]; then \
         echo "reader is '$installed', the recording was made by '$EXPECTED_READER_VERSION'" >&2; \
         echo "Every threshold in recordings/ was derived from that reader, and the extraction" >&2; \
         echo "handler looks its thresholds up by the reader's exact identity — so this image" >&2; \
         echo "would deploy and then fail to find an artefact, three layers away from here." >&2; \
         echo "Re-record through .github/workflows/record.yml, read the movement, and commit" >&2; \
         echo "the recording and this ARG together. Never widen the comparison." >&2; \
         exit 1; \
       fi \
    && for language in eng ell nld deu fra; do \
         tesseract --list-langs 2>&1 | grep -qx "$language" || { \
           echo "language data for '$language' is missing." >&2; \
           echo "A reader without it does not fail on those pages — it reads them in the" >&2; \
           echo "wrong script and returns confident nonsense." >&2; \
           exit 1; }; \
       done

ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}

# **Everything `pyproject.toml` names, not only the code.** It declares `readme = "README.md"`
# and `license = { file = "LICENSE" }`, and the build refuses without either: *"License file
# does not exist"*, then *"Readme file does not exist"*, one per cycle.
#
# Two builds were spent discovering them one at a time, which is its own small lesson: the
# packaging metadata is a list of files this image needs and nothing was reading it. It is read
# now — `grep -E "readme|license|file =" pyproject.toml` is the whole check — and both are here.
# Neither is imported by any module, which is exactly why a container is where it fails and a
# developer machine with the full checkout is where it never does.
COPY pyproject.toml README.md LICENSE ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/
# The contracts travel with the image. They are the source of truth for which fields exist and
# how each is compared, and a deployment whose code and contracts came from different commits
# would apply one version's rules to another version's fields.
COPY contracts/ ${LAMBDA_TASK_ROOT}/contracts/

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir "${LAMBDA_TASK_ROOT}" \
    && python -m pip install --no-cache-dir pillow numpy pypdfium2 awslambdaric boto3

ENV PYTHONPATH="${LAMBDA_TASK_ROOT}/src" \
    CONTRACTS_DIR="${LAMBDA_TASK_ROOT}/contracts"

# The runtime interface client, which is what an AWS base image would have provided. Overridden
# per function by the estate: `read_tier0.handler` and `provenance_gate.handler` share this
# image and differ only in the command.
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["manifest.handlers.read_tier0.handler"]
