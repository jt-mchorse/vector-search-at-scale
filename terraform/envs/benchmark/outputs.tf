output "scale_tier" {
  description = "Scale tier this deployment is sized for."
  value       = var.scale_tier
}

output "instance_type" {
  description = "EC2 instance type used by every backend in this deployment."
  value       = local.tier.instance_type
}

output "ami_id" {
  description = "AMI ID resolved (or pinned)."
  value       = local.resolved_ami_id
}

output "endpoints" {
  description = "Per-backend connection info. Use private_ip from inside the VPC."
  value = {
    pgvector = {
      private_ip = module.pgvector.private_ip
      public_ip  = module.pgvector.public_ip
      port       = 5432
      protocol   = "postgres"
    }
    qdrant = {
      private_ip = module.qdrant.private_ip
      public_ip  = module.qdrant.public_ip
      rest_port  = 6333
      grpc_port  = 6334
    }
    weaviate = {
      private_ip = module.weaviate.private_ip
      public_ip  = module.weaviate.public_ip
      rest_port  = 8080
      grpc_port  = 50051
    }
  }
}
