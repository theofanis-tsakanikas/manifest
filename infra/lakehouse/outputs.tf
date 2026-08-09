output "database" {
  value = aws_glue_catalog_database.records.name
}

output "lake_bucket" {
  value = aws_s3_bucket.lake.id
}

output "workgroup" {
  value = aws_athena_workgroup.analysis.name
}
