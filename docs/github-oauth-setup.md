# GitHub OAuth App Setup

This guide walks through creating and configuring a GitHub OAuth App for SLEAP-RTC authentication.

## Step 1: Create OAuth App

1. Go to your GitHub organization settings:
   - `https://github.com/organizations/YOUR_ORG/settings/applications`
   - Or: Organization → Settings → Developer settings → OAuth Apps

2. Click **"New OAuth App"**

3. Fill in the application details:

   | Field | Value |
   |-------|-------|
   | **Application name** | `SLEAP-RTC` |
   | **Homepage URL** | `https://YOUR_ORG.github.io/sleap-rtc-dashboard/` |
   | **Application description** | `Authentication for SLEAP-RTC remote training` |
   | **Authorization callback URL** | `https://YOUR_ORG.github.io/sleap-rtc-dashboard/callback.html` |

   > **Note:** Replace `YOUR_ORG` with your actual GitHub organization name.

4. Click **"Register application"**

## Step 2: Get Credentials

After creating the app:

1. **Client ID**: Copy and save this (it's public, shown on the app page)
   - Example: `Iv1.a1b2c3d4e5f6g7h8`

2. **Client Secret**: Click "Generate a new client secret"
   - **Save this immediately** - it's only shown once!
   - Example: `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0`

## Step 3: Configure Signaling Server

Add these environment variables to your EC2 instance:

```bash
# SSH to your EC2 instance
ssh ec2-user@ec2-52-9-213-137.us-west-1.compute.amazonaws.com

# Add to environment (edit /etc/environment or your shell profile)
export GITHUB_CLIENT_ID="Iv1.a1b2c3d4e5f6g7h8"
export GITHUB_CLIENT_SECRET="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
export GITHUB_REDIRECT_URI="https://YOUR_ORG.github.io/sleap-rtc-dashboard/callback.html"
```

Or if using systemd service:

```bash
# Edit the service file
sudo systemctl edit sleap-rtc-signaling

# Add environment variables
[Service]
Environment="GITHUB_CLIENT_ID=Iv1.a1b2c3d4e5f6g7h8"
Environment="GITHUB_CLIENT_SECRET=your_secret_here"
Environment="GITHUB_REDIRECT_URI=https://YOUR_ORG.github.io/sleap-rtc-dashboard/callback.html"

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart sleap-rtc-signaling
```

## Step 4: Verify Configuration

Test that the OAuth app is configured correctly:

```bash
# On the EC2 instance, verify environment variables
echo $GITHUB_CLIENT_ID
echo $GITHUB_REDIRECT_URI
# Don't echo the secret in logs!
```

## OAuth Flow Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │     │   GitHub    │     │  Dashboard  │     │  Signaling  │
│   (User)    │     │   OAuth     │     │  (GH Pages) │     │   Server    │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │                   │
       │ 1. Click Login    │                   │                   │
       │──────────────────>│                   │                   │
       │                   │                   │                   │
       │ 2. GitHub Login   │                   │                   │
       │<─────────────────>│                   │                   │
       │                   │                   │                   │
       │ 3. Redirect with code                 │                   │
       │───────────────────────────────────────>                   │
       │                   │                   │                   │
       │                   │                   │ 4. POST /api/auth/github/callback
       │                   │                   │──────────────────>│
       │                   │                   │                   │
       │                   │ 5. Exchange code  │                   │
       │                   │<──────────────────────────────────────│
       │                   │                   │                   │
       │                   │ 6. Access token   │                   │
       │                   │──────────────────────────────────────>│
       │                   │                   │                   │
       │                   │                   │ 7. JWT token      │
       │                   │                   │<──────────────────│
       │                   │                   │                   │
       │ 8. Store JWT, show UI                 │                   │
       │<──────────────────────────────────────│                   │
       │                   │                   │                   │
```

## Security Notes

1. **Client Secret**: Never expose in client-side code (dashboard). Only the signaling server should have it.

2. **Callback URL**: Must match exactly what's registered in GitHub. Include trailing slashes if configured.

3. **Scopes**: We request `read:user` scope only (minimal permissions).

4. **State Parameter**: The dashboard should generate a random state parameter to prevent CSRF.

## Troubleshooting

### "redirect_uri mismatch"
- Verify callback URL matches exactly (including https/http, trailing slash)
- Check that GitHub Pages is deployed and accessible

### "bad_verification_code"
- Authorization codes expire in 10 minutes
- Codes can only be used once

### "access_denied"
- User cancelled the authorization
- User doesn't have access to the organization (if restricted)

## Local Development

For local testing, create a separate OAuth app with:
- **Homepage URL**: `http://localhost:3000`
- **Callback URL**: `http://localhost:3000/callback.html`

Use different environment variables:
```bash
export GITHUB_CLIENT_ID_DEV="your_dev_client_id"
export GITHUB_CLIENT_SECRET_DEV="your_dev_secret"
export GITHUB_REDIRECT_URI_DEV="http://localhost:3000/callback.html"
```
