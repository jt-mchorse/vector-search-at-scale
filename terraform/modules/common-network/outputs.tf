output "vpc_id" {
  description = "ID of the benchmark VPC."
  value       = aws_vpc.this.id
}

output "subnet_id" {
  description = "ID of the single public subnet."
  value       = aws_subnet.public.id
}

output "ssh_security_group_id" {
  description = "Security-group ID granting SSH ingress (only if ssh_ingress_cidrs is non-empty)."
  value       = aws_security_group.ssh.id
}
