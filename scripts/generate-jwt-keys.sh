#!/bin/bash
set -e

# Generate RS256 key pair for JWT signing
# This script creates:
# 1. Private key (for signing JWTs on the signaling server)
# 2. Public key (for verifying JWTs on dashboard/clients)

OUTPUT_DIR="${1:-.}"
KEY_NAME="sleap-rtc-jwt"

echo "Generating RS256 key pair for SLEAP-RTC JWT signing..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Generate private key (2048-bit RSA)
PRIVATE_KEY_FILE="$OUTPUT_DIR/${KEY_NAME}-private.pem"
echo "Generating private key: $PRIVATE_KEY_FILE"
openssl genpkey -algorithm RSA -out "$PRIVATE_KEY_FILE" -pkeyopt rsa_keygen_bits:2048 2>/dev/null

# Set restrictive permissions on private key
chmod 600 "$PRIVATE_KEY_FILE"
echo "✓ Private key generated (permissions: 600)"

# Extract public key
PUBLIC_KEY_FILE="$OUTPUT_DIR/${KEY_NAME}-public.pem"
echo "Extracting public key: $PUBLIC_KEY_FILE"
openssl rsa -pubout -in "$PRIVATE_KEY_FILE" -out "$PUBLIC_KEY_FILE" 2>/dev/null
echo "✓ Public key extracted"

echo ""
echo "============================================"
echo "JWT key pair generated successfully!"
echo "============================================"
echo ""
echo "Files created:"
echo "  Private key: $PRIVATE_KEY_FILE (KEEP SECRET!)"
echo "  Public key:  $PUBLIC_KEY_FILE (can be shared)"
echo ""

# Output keys in environment variable format
echo "============================================"
echo "Environment Variable Format"
echo "============================================"
echo ""
echo "Add these to your signaling server environment:"
echo ""
echo "# Private key (for signing - KEEP SECRET)"
echo "SLEAP_JWT_PRIVATE_KEY=\"$(cat "$PRIVATE_KEY_FILE" | tr '\n' '|' | sed 's/|$//')\""
echo ""
echo "# Public key (for verification - can be shared)"
echo "SLEAP_JWT_PUBLIC_KEY=\"$(cat "$PUBLIC_KEY_FILE" | tr '\n' '|' | sed 's/|$//')\""
echo ""
echo "Note: Newlines are replaced with '|' for single-line env vars."
echo "The server code should replace '|' back to '\\n' when loading."
echo ""

# Also output as base64 (alternative format)
echo "============================================"
echo "Base64 Format (alternative)"
echo "============================================"
echo ""
echo "SLEAP_JWT_PRIVATE_KEY_B64=\"$(base64 < "$PRIVATE_KEY_FILE" | tr -d '\n')\""
echo ""
echo "SLEAP_JWT_PUBLIC_KEY_B64=\"$(base64 < "$PUBLIC_KEY_FILE" | tr -d '\n')\""
echo ""

echo "============================================"
echo "Next Steps"
echo "============================================"
echo ""
echo "1. Copy the environment variables to your EC2 instance"
echo "2. Add them to /etc/environment or your systemd service"
echo "3. Deploy the public key to your GitHub Pages dashboard"
echo "4. Keep the private key file secure and backed up"
echo ""
echo "WARNING: The private key should NEVER be committed to git!"
echo "         Add *.pem to your .gitignore"
echo ""
