resource "aws_security_group" "service" {
  name        = "${var.name}-service"
  description = "Qdrant ports (6333 REST, 6334 gRPC) ingress, restricted to inside-VPC."
  vpc_id      = var.vpc_id

  ingress {
    description = "Qdrant REST from inside the VPC"
    from_port   = 6333
    to_port     = 6333
    protocol    = "tcp"
    cidr_blocks = ["10.42.0.0/16"]
  }

  ingress {
    description = "Qdrant gRPC from inside the VPC"
    from_port   = 6334
    to_port     = 6334
    protocol    = "tcp"
    cidr_blocks = ["10.42.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name}-service"
  })
}

data "aws_subnet" "this" {
  id = var.subnet_id
}

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.this.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  iops              = var.data_volume_iops
  throughput        = var.data_volume_throughput_mibps

  tags = merge(var.tags, {
    Name = "${var.name}-data"
  })
}

resource "aws_instance" "this" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.service.id, var.ssh_security_group_id]
  associate_public_ip_address = true
  key_name                    = var.key_name

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    image_tag = var.image_tag
  })

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = merge(var.tags, {
    Name    = var.name
    Backend = "qdrant"
  })
}

resource "aws_volume_attachment" "data" {
  device_name  = "/dev/sdf"
  volume_id    = aws_ebs_volume.data.id
  instance_id  = aws_instance.this.id
  force_detach = true
}
