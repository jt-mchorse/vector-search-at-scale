output "instance_id" {
  description = "EC2 instance ID running qdrant."
  value       = aws_instance.this.id
}

output "private_ip" {
  description = "Private IP — use this from inside the VPC. REST: http://<private_ip>:6333  gRPC: <private_ip>:6334"
  value       = aws_instance.this.private_ip
}

output "public_ip" {
  description = "Public IP — for SSH only. Service ports are VPC-internal."
  value       = aws_instance.this.public_ip
}

output "data_volume_id" {
  description = "EBS volume ID backing /qdrant/storage."
  value       = aws_ebs_volume.data.id
}
