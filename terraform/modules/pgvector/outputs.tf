output "instance_id" {
  description = "EC2 instance ID running pgvector."
  value       = aws_instance.this.id
}

output "private_ip" {
  description = "Private IP — use this from another instance in the same VPC. Connection string: postgresql://postgres:<password>@<private_ip>:5432/bench"
  value       = aws_instance.this.private_ip
}

output "public_ip" {
  description = "Public IP — used by the operator for SSH. Service port is NOT exposed publicly (security group is VPC-internal only)."
  value       = aws_instance.this.public_ip
}

output "data_volume_id" {
  description = "EBS volume ID backing /var/lib/postgresql."
  value       = aws_ebs_volume.data.id
}
