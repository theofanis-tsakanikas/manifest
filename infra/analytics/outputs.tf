output "workgroup" {
  value = aws_redshiftserverless_workgroup.marts.workgroup_name
}

output "namespace" {
  value = aws_redshiftserverless_namespace.marts.namespace_name
}
