provider "aws" {
  region     = "us-west-2" # Change this to your preferred region
  access_key = "access_key"
  secret_key = "secret_key"
  token      = "token"
}

resource "aws_security_group" "allow_http" {
  name = "allow-8080"

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "docker_server" {
  ami             = "ami-00c257e12d6828491" # us-west-2 ubuntu id
  instance_type   = "t2.micro"
  security_groups = [aws_security_group.allow_http.name]
  key_name        = "aattapu-webrtc-ec2-server" # Replace with your EC2 key pair

  user_data = <<-EOF
              #!/bin/bash
              apt update -y
              apt install -y docker.io
              systemctl start docker
              systemctl enable docker
              docker login ghcr.io -u USERNAME -p GITHUB_TOKEN
              docker pull ghcr.io/talmolab/webrtc-server:linux-amd64-05eb8a705d2a86144b3e5174e3581027124d83bd
              docker run -d -p 8080:8080 ghcr.io/talmolab/webrtc-server:linux-amd64-05eb8a705d2a86144b3e5174e3581027124d83bd
              EOF

  tags = {
    Name = "docker-server"
  }
}
