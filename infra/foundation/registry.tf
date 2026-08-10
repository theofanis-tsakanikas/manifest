# The registry the reader image lives in.
#
# **Here rather than in `infra/extraction`, and the reason is ordering.** The image has to exist
# before a function can be created from it, so a registry owned by the layer that consumes it
# would have to be created by the same apply that needs it already populated. The first run
# fails at `docker push` — four minutes in, with the environment approval already spent — and
# every subsequent run works, which is the worst kind of bug to leave in a deploy path.
#
# It also belongs here on its own merits: a registry is shared, long-lived infrastructure like
# the KMS keys and the data zones beside it, not a detail of one pipeline.

resource "aws_ecr_repository" "reader" {
  name                 = "${var.project}-reader"
  image_tag_mutability = "IMMUTABLE"

  # A repository holding images refuses to be deleted. On a portfolio estate that is a teardown
  # that stops halfway and leaves a registry standing — and the images are rebuildable from the
  # Dockerfile in one command, so there is nothing here that deleting loses.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }

  tags = { "${var.project}:expires-at" = var.expires_at }
}

# Keep the image the running functions point at, and nothing else.
#
# `IMMUTABLE` above means a tag never moves, so "latest" cannot silently become a different
# reader — which matters here more than in most systems: every threshold in this repository was
# derived from one build of that binary, and a tag that quietly repointed would move all of them
# without a diff.
resource "aws_ecr_lifecycle_policy" "reader" {
  repository = aws_ecr_repository.reader.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after a day; a tagged one is referenced by a deploy."
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}
