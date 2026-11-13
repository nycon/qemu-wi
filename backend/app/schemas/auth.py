"""
Pydantic schemas for authentication endpoints.
"""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8)
    totp_code: str | None = Field(None, min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    temp_token: str | None = None


class RefreshRequest(BaseModel):
    # Optional when the refresh token is delivered via httpOnly cookie.
    refresh_token: str | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=150, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=255)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    email: EmailStr | None = None


class UserSshKeyCreate(BaseModel):
    public_key: str = Field(..., min_length=32, max_length=8192)
    name: str | None = Field(None, max_length=120)


# --- 2FA (TOTP) ---

class TwoFactorSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_code_base64: str


class TwoFactorVerifyRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6)


class TwoFactorVerifyLoginRequest(BaseModel):
    temp_token: str
    totp_code: str = Field(..., min_length=6, max_length=6)


class TwoFactorDisableRequest(BaseModel):
    current_password: str
    totp_code: str = Field(..., min_length=6, max_length=6)


class TwoFactorStatusResponse(BaseModel):
    enabled: bool
