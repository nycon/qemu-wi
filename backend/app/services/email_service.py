"""
Email notification service.

Sends HTML emails via SMTP for important system events:
- VM status changes (error, provisioning failed)
- Backup completed / failed
- Quota warnings
- Security alerts (login from new IP, 2FA changes)
"""

import asyncio
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

_LAST_SMTP_ERROR: str = ""


def get_last_smtp_error() -> str:
    return _LAST_SMTP_ERROR


def _smtp_configured() -> bool:
    return bool(settings.SMTP_ENABLED and settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def _build_html(title: str, body_html: str, footer_text: str = "") -> str:
    """Build a styled HTML email wrapper."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;padding:40px 20px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.06);overflow:hidden;">
  <!-- Header -->
  <tr><td style="background-color:#1e293b;padding:24px 32px;">
    <table width="100%"><tr>
      <td><span style="color:#818cf8;font-size:20px;font-weight:700;">&#9881; QEMU Interface</span></td>
      <td align="right"><span style="color:#94a3b8;font-size:13px;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</span></td>
    </tr></table>
  </td></tr>
  <!-- Title -->
  <tr><td style="padding:28px 32px 8px;">
    <h1 style="margin:0;font-size:20px;font-weight:700;color:#1e293b;">{title}</h1>
  </td></tr>
  <!-- Body -->
  <tr><td style="padding:8px 32px 28px;font-size:14px;line-height:1.7;color:#475569;">
    {body_html}
  </td></tr>
  <!-- Footer -->
  <tr><td style="padding:20px 32px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8;">
    {footer_text or 'This is an automated notification from your QEMU Interface installation.'}
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _send_email_sync(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email synchronously (call from thread pool)."""
    global _LAST_SMTP_ERROR
    _LAST_SMTP_ERROR = ""

    if not _smtp_configured():
        _LAST_SMTP_ERROR = "SMTP is not fully configured (enabled/host/from_email)."
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        use_implicit_ssl = int(settings.SMTP_PORT) == 465
        context = ssl.create_default_context()
        if settings.SMTP_ALLOW_INVALID_CERT:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        if use_implicit_ssl:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=context) as server:
                server.ehlo()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        elif settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.ehlo()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

        print(f"[Email] Sent to {to_email}: {subject}")
        return True

    except Exception as e:
        _LAST_SMTP_ERROR = str(e)
        print(f"[Email] Failed to send to {to_email}: {e}")
        return False


async def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email asynchronously."""
    if not _smtp_configured():
        return False
    return await asyncio.to_thread(_send_email_sync, to_email, subject, html_body)


def _status_badge(status: str, color: str) -> str:
    bg_colors = {
        "red": "#fef2f2", "green": "#f0fdf4", "yellow": "#fefce8",
        "blue": "#eff6ff", "gray": "#f9fafb",
    }
    text_colors = {
        "red": "#dc2626", "green": "#16a34a", "yellow": "#ca8a04",
        "blue": "#2563eb", "gray": "#6b7280",
    }
    bg = bg_colors.get(color, bg_colors["gray"])
    fg = text_colors.get(color, text_colors["gray"])
    return f'<span style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;background:{bg};color:{fg};">{status}</span>'


def _info_row(label: str, value: str) -> str:
    return f"""<tr>
      <td style="padding:6px 12px;font-size:13px;color:#94a3b8;white-space:nowrap;">{label}</td>
      <td style="padding:6px 12px;font-size:13px;color:#1e293b;font-weight:500;">{value}</td>
    </tr>"""


# ─── Notification Templates ──────────────────────────────────────────────────


async def notify_vm_error(to_email: str, vm_name: str, vm_id: str, error_msg: str, tenant_name: str = ""):
    """Notify user when a VM enters error state."""
    body = f"""
    <p>A virtual machine has encountered an error:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("VM", vm_name)}
      {_info_row("Status", _status_badge("Error", "red"))}
      {_info_row("VM ID", vm_id[:8] + "...")}
      {_info_row("Tenant", tenant_name or "—")}
      {_info_row("Error", f'<span style="color:#dc2626;">{error_msg[:200]}</span>')}
    </table>
    <p>Please check the VM details in the web interface for more information.</p>
    """
    html = _build_html("VM Error", body)
    await send_email(to_email, f"[Alert] VM \"{vm_name}\" – Error", html)


async def notify_vm_created(to_email: str, vm_name: str, vm_id: str, ip_address: str = "", tenant_name: str = ""):
    """Notify user when a VM is successfully created."""
    body = f"""
    <p>Your virtual machine has been created and is now running:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("VM", vm_name)}
      {_info_row("Status", _status_badge("Running", "green"))}
      {_info_row("IP Address", ip_address or "—")}
      {_info_row("VM ID", vm_id[:8] + "...")}
      {_info_row("Tenant", tenant_name or "—")}
    </table>
    <p>You can access the VNC console from the VM detail page.</p>
    """
    html = _build_html("VM Created", body)
    await send_email(to_email, f"VM \"{vm_name}\" – Ready", html)


async def notify_backup_completed(to_email: str, vm_name: str, backup_name: str, file_size: str = ""):
    """Notify user when a backup completes successfully."""
    body = f"""
    <p>A backup has been completed successfully:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("VM", vm_name)}
      {_info_row("Backup", backup_name)}
      {_info_row("Status", _status_badge("Completed", "green"))}
      {_info_row("Size", file_size or "—")}
    </table>
    """
    html = _build_html("Backup Completed", body)
    await send_email(to_email, f"Backup \"{backup_name}\" – Completed", html)


async def notify_backup_failed(to_email: str, vm_name: str, backup_name: str, error_msg: str):
    """Notify user when a backup fails."""
    body = f"""
    <p>A backup has failed:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("VM", vm_name)}
      {_info_row("Backup", backup_name)}
      {_info_row("Status", _status_badge("Failed", "red"))}
      {_info_row("Error", f'<span style="color:#dc2626;">{error_msg[:200]}</span>')}
    </table>
    <p>Please check the backup logs or try again.</p>
    """
    html = _build_html("Backup Failed", body)
    await send_email(to_email, f"[Alert] Backup \"{backup_name}\" – Failed", html)


async def notify_quota_warning(to_email: str, tenant_name: str, resource: str, used: int, limit: int, percent: int):
    """Notify admin when a tenant approaches quota limits."""
    color = "red" if percent >= 90 else "yellow"
    body = f"""
    <p>A tenant is approaching its resource quota:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("Tenant", tenant_name)}
      {_info_row("Resource", resource)}
      {_info_row("Usage", f"{used} / {limit}")}
      {_info_row("Utilization", _status_badge(f"{percent}%", color))}
    </table>
    <p>Consider increasing the quota or reviewing resource usage.</p>
    """
    html = _build_html("Quota Warning", body)
    await send_email(to_email, f"[Warning] Quota {percent}% – {tenant_name} ({resource})", html)


async def notify_login_new_ip(to_email: str, username: str, ip_address: str, user_agent: str = ""):
    """Notify user about login from a new IP address."""
    body = f"""
    <p>A new login was detected on your account:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("User", username)}
      {_info_row("IP Address", ip_address)}
      {_info_row("Browser", user_agent[:100] if user_agent else "—")}
      {_info_row("Time", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}
    </table>
    <p>If this wasn't you, please change your password immediately and enable 2FA.</p>
    """
    html = _build_html("New Login Detected", body, "If you did not perform this login, secure your account immediately.")
    await send_email(to_email, f"[Security] New login from {ip_address}", html)


async def notify_2fa_changed(to_email: str, username: str, enabled: bool):
    """Notify user when 2FA is enabled or disabled."""
    action = "enabled" if enabled else "disabled"
    color = "green" if enabled else "yellow"
    body = f"""
    <p>Two-factor authentication has been {action} on your account:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#fafafa;border-radius:8px;">
      {_info_row("User", username)}
      {_info_row("2FA", _status_badge("Enabled" if enabled else "Disabled", color))}
      {_info_row("Time", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}
    </table>
    {"<p>Your account is now protected with an additional security layer.</p>" if enabled else "<p>Your account is no longer protected by 2FA. Consider re-enabling it for better security.</p>"}
    """
    html = _build_html(f"2FA {action.capitalize()}", body)
    await send_email(to_email, f"[Security] 2FA {action}", html)


async def send_test_email(to_email: str) -> bool:
    """Send a test email to verify SMTP configuration."""
    body = """
    <p>This is a test email from your QEMU Interface installation.</p>
    <p style="margin:16px 0;padding:16px;background:#f0fdf4;border-radius:8px;text-align:center;">
      <span style="color:#16a34a;font-weight:600;font-size:16px;">&#10003; SMTP is working correctly!</span>
    </p>
    <p>You will now receive notifications for important system events.</p>
    """
    html = _build_html("Test Email", body)
    return await send_email(to_email, "QEMU Interface – Test Email", html)
