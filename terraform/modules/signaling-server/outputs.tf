output "public_ip" {
  description = "Elastic IP address"
  value       = aws_eip.signaling.public_ip
}

output "elastic_ip" {
  description = "Elastic IP address (alias for public_ip)"
  value       = aws_eip.signaling.public_ip
}

output "public_dns" {
  description = "AWS-provided public DNS for the Elastic IP"
  value       = aws_eip.signaling.public_dns
}

output "websocket_url" {
  description = "WebSocket URL for clients"
  value       = "ws://${aws_eip.signaling.public_ip}:${var.websocket_port}"
}

output "http_url" {
  description = "HTTP API URL"
  value       = "http://${aws_eip.signaling.public_ip}:${var.http_port}"
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.signaling.id
}

output "https_url" {
  description = "HTTPS URL for signaling server (if enabled)"
  value       = var.enable_https ? "https://${var.duckdns_subdomain}.duckdns.org" : null
}
