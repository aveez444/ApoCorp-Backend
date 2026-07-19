# apps/logistics/state_codes.py
#
# Single source of truth for GST state-code resolution.
#
# IRP requires the 2-digit numeric state code (e.g. '27' for Maharashtra) in
# every Stcd field of the e-invoice payload, and it's also what determines
# CGST+SGST (intra-state) vs IGST (inter-state) on generated IRNs.
#
# Various parts of the app store "state" as a plain name (customer.state,
# TenantLetterhead.company_state, SalesInvoice.state_code populated from
# customer.state) — this module resolves either a name or an already-numeric
# code to the canonical 2-digit code, so gsp_client.py and einvoice_views.py
# never diverge on the mapping again.
#
# Frontend note: whatever your state dropdown/field sends (a name or a code)
# gets normalized here — you don't need to pre-resolve it client-side, just
# be consistent about what you store on the invoice.

from __future__ import annotations

STATE_CODES: dict[str, str] = {
    'jammu and kashmir': '01', 'himachal pradesh': '02', 'punjab': '03',
    'chandigarh': '04', 'uttarakhand': '05', 'haryana': '06',
    'delhi': '07', 'rajasthan': '08', 'uttar pradesh': '09',
    'bihar': '10', 'sikkim': '11', 'arunachal pradesh': '12',
    'nagaland': '13', 'manipur': '14', 'mizoram': '15',
    'tripura': '16', 'meghalaya': '17', 'assam': '18',
    'west bengal': '19', 'jharkhand': '20', 'odisha': '21',
    'chhattisgarh': '22', 'madhya pradesh': '23', 'gujarat': '24',
    'daman and diu': '25', 'dadra and nagar haveli': '26',
    'maharashtra': '27', 'andhra pradesh': '28', 'karnataka': '29',
    'goa': '30', 'lakshadweep': '31', 'kerala': '32',
    'tamil nadu': '33', 'puducherry': '34', 'andaman and nicobar': '35',
    'telangana': '36', 'andhra pradesh (new)': '37',
    'ladakh': '38',
    'other territory': '97', 'other country': '96',
}


def resolve_state_code(value) -> str:
    """
    Normalize a state value to IRP's 2-digit numeric code.

    Accepts:
      - an already-numeric code   ('27' -> '27', '5' -> '05')
      - a state name              ('Maharashtra', 'maharashtra')
      - '' / None                 -> ''

    Returns '' if the value can't be resolved. Callers going to IRP should
    treat an empty result as a hard error, not silently default to some
    specific state — a wrong silent default produces either a wrong tax
    split (CGST/SGST vs IGST) or an IRP rejection that's much harder to
    trace back to its source than a clear error at submission time.
    """
    if not value:
        return ''
    text = str(value).strip()
    if not text:
        return ''
    if text.isdigit():
        return text.zfill(2)
    return STATE_CODES.get(text.lower(), '')