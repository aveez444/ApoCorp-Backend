# apps/logistics/einvoice_models.py
#
# Two models:
#   1. TenantGSPConfig   — one row per tenant; stores their GSP credentials
#                          (GSTIN, GSP client-id/secret, username/password).
#                          Credentials are stored encrypted — use django-fernet-fields
#                          or env-based encryption; plain text shown here for clarity.
#
#   2. EInvoiceRecord    — one row per SalesInvoice once it has been submitted to
#                          the IRP / GSP and an IRN is issued.
#                          Stores the raw JSON payload sent, the raw JSON response,
#                          IRN, signed QR string, ack number, ack datetime, and status.
#
# Migration hint:
#   python manage.py makemigrations logistics
#   python manage.py migrate

from django.db import models
from apps.tenants.models import Tenant
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# GSP / IRP credentials — one per tenant
# ─────────────────────────────────────────────────────────────────────────────

class TenantGSPConfig(models.Model):
    """
    Stores the GSP (GST Suvidha Provider) API credentials for a tenant.

    India's e-invoice flow:
        Your system  →  GSP API  →  IRP (Invoice Registration Portal, NIC)
                                           ↓
                                     IRN + signed QR  ←────────────────────

    Popular GSPs (all expose a similar REST API):
        • Masters India  — https://api.mastersindia.co
        • Clear (ClearTax) — https://einvoice.clear.in
        • Karvy / KFin
        • Tata Consultancy Services

    For SANDBOX testing use the NIC sandbox directly:
        https://einv-apisandbox.nic.in

    Fill in whichever fields your chosen GSP requires.
    The gsp_provider field is a hint for the client code to pick the right
    adapter (see gsp_client.py).
    """

    GSP_CHOICES = [
        ('NIC_SANDBOX', 'NIC Sandbox (testing)'),
        ('NIC_PROD',    'NIC Production'),
        ('MASTERS',     'Masters India'),
        ('CLEAR',       'ClearTax / Clear'),
        ('GENERIC',     'Generic GSP (custom base URL)'),
    ]

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name='gsp_config',
    )

    gsp_provider = models.CharField(
        max_length=20,
        choices=GSP_CHOICES,
        default='NIC_SANDBOX',
        help_text='Which GSP / IRP endpoint to use.',
    )

    # Override base URL only when gsp_provider == GENERIC
    gsp_base_url = models.URLField(
        blank=True,
        help_text=(
            'Leave blank for known providers. '
            'Required only when gsp_provider is GENERIC.'
        ),
    )

    # The GSTIN of the supplier (this tenant's company)
    gstin = models.CharField(
        max_length=15,
        help_text='15-character GSTIN of the supplier.',
    )

    # GSP-issued API credentials
    client_id = models.CharField(
        max_length=255, blank=True,
        help_text='GSP API client_id (issued by your GSP).',
    )
    client_secret = models.CharField(
        max_length=255, blank=True,
        help_text='GSP API client_secret (issued by your GSP). Store encrypted in production.',
    )

    # IRP / NIC portal username & password (the ones you log into einvoice1.gst.gov.in with)
    irp_username = models.CharField(max_length=255, blank=True)
    irp_password = models.CharField(
        max_length=255, blank=True,
        help_text='IRP portal password. Store encrypted in production.',
    )

    # Cached auth token (refreshed automatically by the GSP client)
    _auth_token      = models.TextField(blank=True, db_column='auth_token')
    _token_expires   = models.DateTimeField(null=True, blank=True, db_column='token_expires')

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tenant GSP Config'

    def __str__(self):
        return f'GSP Config — {self.tenant.company_name} ({self.gsp_provider})'

    # ── Token helpers ─────────────────────────────────────────────────────────

    @property
    def auth_token(self):
        return self._auth_token

    def cache_token(self, token: str, expires_at):
        self._auth_token   = token
        self._token_expires = expires_at
        self.save(update_fields=['_auth_token', '_token_expires'])

    def is_token_valid(self) -> bool:
        if not self._auth_token or not self._token_expires:
            return False
        return timezone.now() < self._token_expires


# ─────────────────────────────────────────────────────────────────────────────
# E-Invoice record — one row per IRN issued
# ─────────────────────────────────────────────────────────────────────────────

class EInvoiceRecord(models.Model):
    """
    Immutable record of the IRN registration for a SalesInvoice.

    Lifecycle
    ---------
    PENDING   → invoice created but not yet submitted to IRP
    SUBMITTED → payload sent to GSP; awaiting response (use for async flows)
    ACTIVE    → IRN issued successfully; invoice is live
    CANCELLED → IRN cancelled via the GSP within the 24-hour window
    FAILED    → GSP returned an error; see error_response for details

    An invoice should have at most ONE ACTIVE EInvoiceRecord.
    Cancellation creates a new row with status=CANCELLED and links back to
    the original via cancelled_irn (the same IRN string).
    """

    STATUS_PENDING   = 'PENDING'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_ACTIVE    = 'ACTIVE'
    STATUS_CANCELLED = 'CANCELLED'
    STATUS_FAILED    = 'FAILED'

    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_ACTIVE,    'Active'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_FAILED,    'Failed'),
    ]

    # ── Relations ─────────────────────────────────────────────────────────────
    invoice = models.OneToOneField(
        'logistics.SalesInvoice',
        on_delete=models.PROTECT,        # never delete an invoice with an IRN
        related_name='einvoice',
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name='einvoices',
    )

    # ── IRP response fields ───────────────────────────────────────────────────
    irn = models.CharField(
        max_length=64, blank=True,
        help_text='Invoice Reference Number — 64-char hash returned by IRP.',
    )
    ack_no = models.CharField(
        max_length=20, blank=True,
        help_text='Acknowledgement number from IRP.',
    )
    ack_date = models.DateTimeField(
        null=True, blank=True,
        help_text='Acknowledgement datetime from IRP (IST).',
    )

    # The signed QR string exactly as returned by IRP.
    # Render this with a QR library (qrcode / segno) in the PDF template.
    signed_qr_data = models.TextField(
        blank=True,
        help_text='Base64/JWT signed QR string returned by IRP. Use this to render the QR code.',
    )

    # The full signed invoice JSON returned by IRP (for audit / re-print)
    signed_invoice = models.JSONField(
        null=True, blank=True,
        help_text='Complete SignedInvoice JSON as returned by IRP.',
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # Raw payload we sent (for debugging / re-submission)
    request_payload = models.JSONField(
        null=True, blank=True,
        help_text='Exact JSON payload submitted to GSP/IRP.',
    )

    # Raw response we received (success or error)
    raw_response = models.JSONField(
        null=True, blank=True,
        help_text='Complete JSON response from GSP/IRP.',
    )

    error_message = models.TextField(
        blank=True,
        help_text='Human-readable error if status=FAILED.',
    )

    # Cancellation
    cancel_reason = models.CharField(max_length=255, blank=True)
    cancel_remarks = models.TextField(blank=True)
    cancelled_at   = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'E-Invoice Record'
        ordering = ['-created_at']

    def __str__(self):
        return f'EInvoice {self.irn or "PENDING"} — {self.invoice.invoice_number}'

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def can_cancel(self):
        """IRN can be cancelled only within 24 hours of ack_date."""
        if not self.is_active or not self.ack_date:
            return False
        delta = timezone.now() - self.ack_date
        return delta.total_seconds() < 86_400   # 24 h