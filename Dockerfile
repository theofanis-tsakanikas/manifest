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

# The reader and its language data. Greek and Dutch are the two the scenario turns on and the
# two no managed service reads — `docs/AWS-CONSTRAINTS.md` — so their absence is not a
# degradation, it is the loss of the only reader that can see those pages at all.
#
# Named individually rather than pulled as a group: an image silently missing one language would
# read those pages in the wrong script and return confident nonsense.
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
    && rm -rf /var/lib/apt/lists/*

# **The version and the languages, asserted rather than hoped for.**
#
# A base image is rebuilt by its publisher and a rebuild can move a package. If that moved the
# reader, every derived threshold would drift on the next deploy — silently, because nothing
# downstream notices a confidence distribution shifting by two points. `make ocr-record` refuses
# to overwrite a recording without an accepted movement report for exactly this reason; this is
# the same rule enforced at the other end.
#
# The check is on the *major.minor* series rather than the patch: a patch release of the reader
# does not move confidences, and pinning to one would make this image unbuildable the week the
# distribution ships a security fix. `EXPECTED_READER_SERIES` is what the recording was made
# with; `make ocr-record` prints the movement if it ever needs to change.
ARG EXPECTED_READER_SERIES=5.5
RUN installed="$(tesseract --version 2>&1 | head -1 | awk '{print $2}')" \
    && case "$installed" in \
         "${EXPECTED_READER_SERIES}"*) : ;; \
         *) echo "reader is $installed, this image expects ${EXPECTED_READER_SERIES}.x" >&2; \
            echo "Every threshold in recordings/ was derived from the expected series. Accept" >&2; \
            echo "the movement with 'make ocr-record' and update EXPECTED_READER_SERIES in the" >&2; \
            echo "same commit — a reader that moved silently moves every number on the board." >&2; \
            exit 1 ;; \
       esac \
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
