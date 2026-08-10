# AWS SSO Authentication for Credential Templates

BNK-Forge v2 now supports full AWS SSO (Single Sign-On) device code authentication for credential templates. This allows you to authenticate with AWS SSO and automatically obtain temporary credentials for your projects.

## Features

- **Device Code Flow**: Modern OAuth2 device authorization flow for SSO authentication
- **Automatic Credential Refresh**: Credentials are automatically refreshed before expiration
- **Secure Storage**: All SSO tokens and credentials are encrypted at rest
- **Multi-Account Support**: Support for multiple AWS accounts and roles via SSO
- **Visual Feedback**: User-friendly UI showing authentication status and expiration

## How It Works

### 1. Configuration
When creating or editing a credential template:
1. Select `sso` as the AWS auth method
2. Enable AWS SSO
3. Provide:
   - **SSO Start URL**: Your AWS SSO portal URL (e.g., `https://my-company.awsapps.com/start`)
   - **SSO Region**: AWS region for SSO (typically `us-east-1`)
   - **Account ID**: The AWS account ID to authenticate with
   - **Role Name**: The IAM role name to assume (e.g., `AdministratorAccess`)

### 2. Authentication
After saving the template configuration:
1. Click the **Shield** icon next to the template
   - **Note**: The Shield button only appears for templates using SSO authentication method
   - Templates using access keys or profile methods will not show the Shield button
2. A device code dialog will appear showing:
   - A verification URL to visit
   - A user code to enter
3. Open the URL in your browser
4. Enter the user code when prompted
5. Complete the AWS SSO authentication in your browser
6. The dialog will automatically close once authentication succeeds

### 3. Credential Flow
Once authenticated, BNK-Forge automatically:
1. Obtains an SSO access token and refresh token
2. Uses the access token to get temporary AWS credentials for your account/role
3. Stores the credentials encrypted in the database
4. Makes them available to projects using this template

### 4. Auto-Refresh
The system automatically refreshes:
- **SSO tokens** before they expire (typically 8 hours)
- **AWS credentials** before they expire (typically 1 hour)
- Projects continue working seamlessly without re-authentication

## Architecture

### Database Schema

New fields added to `cloud_credential_templates` table:
```sql
aws_sso_access_token_encrypted   TEXT    -- Encrypted SSO access token
aws_sso_refresh_token_encrypted  TEXT    -- Encrypted refresh token
aws_sso_client_id                VARCHAR -- SSO client ID
aws_sso_client_secret_encrypted  TEXT    -- Encrypted client secret
aws_sso_token_expiry             TIMESTAMP -- When SSO token expires
aws_sso_authenticated_at         TIMESTAMP -- When auth completed
```

### API Endpoints

#### `POST /api/credential-templates/{id}/authenticate-sso`
Initiates the device code flow.

**Response:**
```json
{
  "success": true,
  "data": {
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://device.sso.us-east-1.amazonaws.com/",
    "verification_uri_complete": "https://device.sso.us-east-1.amazonaws.com/?user_code=ABCD-EFGH",
    "expires_in": 900,
    "interval": 5,
    "device_code": "..."
  }
}
```

#### `POST /api/credential-templates/{id}/poll-sso`
Polls for authentication completion.

**Request:**
```json
{
  "device_code": "..."
}
```

**Response (pending):**
```json
{
  "success": false,
  "pending": true,
  "message": "Authorization pending"
}
```

**Response (success):**
```json
{
  "success": true,
  "message": "SSO authentication successful",
  "data": {
    "authenticated_at": "2026-01-27T21:00:00",
    "token_expiry": "2026-01-28T05:00:00",
    "credentials_expiry": "2026-01-27T22:00:00",
    "has_credentials": true
  }
}
```

#### `POST /api/credential-templates/{id}/refresh-sso`
Manually refreshes SSO credentials.

#### `GET /api/credential-templates/{id}/sso-status`
Gets SSO authentication status.

**Response:**
```json
{
  "success": true,
  "sso_enabled": true,
  "is_authenticated": true,
  "authenticated_at": "2026-01-27T21:00:00",
  "token_expired": false,
  "token_expiry": "2026-01-28T05:00:00",
  "credentials_expired": false,
  "credentials_expiry": "2026-01-27T22:00:00",
  "has_credentials": true,
  "can_refresh": true,
  "needs_reauth": false
}
```

## Usage in Projects

Once a template is authenticated with SSO, projects using that template automatically receive the AWS credentials:

```python
# credentials_service.py automatically handles SSO templates
env = get_cloud_credentials_env(project, db)
# env now contains:
# AWS_ACCESS_KEY_ID
# AWS_SECRET_ACCESS_KEY
# AWS_SESSION_TOKEN
# AWS_DEFAULT_REGION
```

## Security Considerations

1. **Encryption**: All sensitive data is encrypted using Fernet encryption:
   - SSO access tokens
   - SSO refresh tokens
   - SSO client secrets
   - AWS credentials

2. **Automatic Expiration**: Credentials are time-limited:
   - SSO tokens typically expire after 8 hours
   - AWS credentials typically expire after 1 hour
   - System automatically refreshes before expiration

3. **Audit Trail**: Authentication timestamps are tracked:
   - `aws_sso_authenticated_at`: When SSO auth completed
   - `aws_sso_token_expiry`: When token expires
   - `aws_credentials_expiry`: When credentials expire

## Troubleshooting

### "Authorization pending" continues indefinitely
- Check if you completed the authentication in your browser
- Verify the user code was entered correctly
- Ensure your AWS SSO session is active

### "Token expired" error
- Click the Shield icon to re-authenticate
- Check if your SSO refresh token is still valid
- May need to re-authenticate if refresh token expired

### Projects can't access AWS resources
- Check template SSO status: `GET /api/credential-templates/{id}/sso-status`
- Verify credentials haven't expired
- Try manual refresh: `POST /api/credential-templates/{id}/refresh-sso`
- Re-authenticate if needed

## Migration from Static Credentials

If you have existing templates with static AWS credentials:

1. **Create new SSO template**:
   - Settings → Auth Templates tab
   - Create new template with SSO enabled
   - Authenticate via device code flow

2. **Update projects**:
   - Go to each project
   - Change credential template to the new SSO template
   - No downtime required

3. **Delete old templates**:
   - Only after all projects are migrated
   - Old templates with static credentials can be removed

## UI Features

### Settings Page
- Navigate to **Settings → Auth Templates** tab
- The Shield authentication button is contextual:
  - ✅ Appears for templates with `aws_auth_method: sso`
  - ❌ Hidden for templates using `access_keys` or `profile` methods
  - Keeps the UI clean and only shows relevant actions

### Visual Indicators
- **Green badge** (>24 hours): Credentials are fresh
- **Yellow badge** (<24 hours): Credentials expiring soon
- **Red badge** (<1 hour or expired): Immediate action needed
- Tooltips show exact expiration time on hover

### Authentication Status
Templates display multiple status indicators:
- **Credentials Configured**: Shows when credentials are stored
- **SSO Authenticated**: Shows when SSO tokens are valid
- **Expiration Badge**: Real-time countdown to credential expiry

## Benefits of SSO

✅ **No static credentials** - No need to store long-lived access keys
✅ **Automatic rotation** - Credentials refresh automatically
✅ **Centralized access** - Manage permissions in AWS SSO
✅ **Audit compliance** - Better audit trail for access
✅ **Multi-account** - Easy access to multiple AWS accounts
✅ **Role-based** - Assume different roles per template

## Limitations

- **Initial Setup**: Requires one-time device code authentication
- **Refresh Dependency**: Requires valid refresh token (typically valid for 90 days)
- **SSO Availability**: Requires AWS SSO to be configured in your AWS organization
- **Session Limits**: AWS SSO has session duration limits (max 12 hours for access token)

## Technical Details

### SSO Flow Sequence

```
1. User clicks "Authenticate SSO" button
   ↓
2. Backend calls AWS SSO OIDC:
   - register_client() → client_id, client_secret
   - start_device_authorization() → device_code, user_code
   ↓
3. Frontend displays verification URL + user code
   ↓
4. User completes auth in browser
   ↓
5. Frontend polls: create_token(device_code)
   - Returns: access_token, refresh_token
   ↓
6. Backend calls SSO:
   - get_role_credentials(access_token)
   - Returns: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
   ↓
7. Credentials stored encrypted
   ↓
8. Projects can now use credentials via credentials_service.py
```

### Auto-Refresh Background Job

The system runs a background job every 5 minutes to check and refresh expiring credentials:

```python
# Runs automatically via APScheduler
def check_and_refresh_credentials():
    - Query all templates with SSO enabled
    - Check if credentials expire in < 10 minutes
    - If expired or expiring:
      - Try refresh with refresh_token
      - If refresh fails, mark as needs_reauth
```

## Related Files

- **Backend**:
  - `backend/routes/credential_templates.py` - SSO API endpoints
  - `backend/services/aws_auth_service.py` - AWS SSO integration
  - `backend/services/credentials_service.py` - Credential loading
  - `backend/models.py` - Database models
  - `backend/alembic/versions/870a6bb4049d_add_sso_token_fields_to_credential_.py` - Migration

- **Frontend**:
  - `frontend-v2/src/components/settings/SSOAuthDialog.tsx` - Auth UI
  - `frontend-v2/src/components/settings/CredentialTemplates.tsx` - Template management
  - `frontend-v2/src/lib/api.ts` - API client
  - `frontend-v2/src/types/index.ts` - TypeScript types

## API Integration

For programmatic access, you can use the REST API:

```bash
# 1. Initiate SSO
curl -X POST http://localhost:8000/api/credential-templates/1/authenticate-sso

# 2. Poll for completion
curl -X POST http://localhost:8000/api/credential-templates/1/poll-sso \
  -H "Content-Type: application/json" \
  -d '{"device_code": "..."}'

# 3. Check status
curl http://localhost:8000/api/credential-templates/1/sso-status

# 4. Manual refresh
curl -X POST http://localhost:8000/api/credential-templates/1/refresh-sso
```
