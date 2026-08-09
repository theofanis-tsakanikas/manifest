output "vpc_id" {
  description = "The private VPC every compute layer runs in."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Subnets with no route to the internet."
  value       = [for subnet in aws_subnet.private : subnet.id]
}

output "endpoint_security_group_id" {
  description = "The group that permits HTTPS to the interface endpoints from inside the VPC."
  value       = aws_security_group.endpoints.id
}

output "data_key_arn" {
  description = "The key every zone is encrypted with."
  value       = aws_kms_key.data.arn
}

output "logs_key_arn" {
  description = "The key log groups are encrypted with."
  value       = aws_kms_key.logs.arn
}

output "bucket_names" {
  description = "The three data zones, by purpose."
  value       = { for name, bucket in aws_s3_bucket.zone : name => bucket.id }
}

output "alerts_topic_arn" {
  description = "Where the budget guard and the expiry rule publish."
  value       = aws_sns_topic.alerts.arn
}
