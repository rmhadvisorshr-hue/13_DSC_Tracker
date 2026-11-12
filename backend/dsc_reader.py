"""Read Indian-CA DSC certificates, no PIN needed.

1. PKCS#11 scan of physically plugged-in tokens (primary).
2. Windows CurrentUser\\My store (fallback). Results are de-duplicated by serial number.

Run `python dsc_reader.py` with the token plugged in to test.
"""
import datetime as dt
import glob
import os
import ssl
import struct
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

try:
    import PyKCS11
except ImportError:  # still works via the Windows store
    PyKCS11 = None

# Matched against issuer CN + O, lowercased, non-alphanumerics removed. (Windows-store scan only.)
CA_KEYWORDS = [
    "mudhra", "capricorn", "vsign", "sify", "pantasign", "safescrypt",
    "xtratrust", "idsign", "care4sign", "ncode", "nsdl", "protean",
    "tcs", "cdac", "indiaca", "idrbt", "nic",
]
EXPIRING_DAYS = 30

# PKCS#11 driver DLL name patterns (case-insensitive globs) for Indian DSC tokens.
PKCS11_DLLS = [
    "*hyp2003*.dll", "*hyperpki*.dll",       # HyperPKI HYP2003
    "eps2003csp11.dll", "eps2003csp11v2.dll",  # Feitian ePass2003 (HyperPKI is an ePass2003 OEM)
    "SignatureP11.dll", "wdpkcs.dll",        # Watchdata ProxKey
    "mtoken_pkcs11.dll", "CryptoIDA_pkcs11.dll", "aetpkss1.dll",  # mToken / SafeNet
    "eTPKCS11.dll",                          # SafeNet eToken
    "ostc1_pkcs11.dll", "castle.dll",
    "ShuttleCsp11_3003.dll",                 # TrustKey / Shuttle
]


def _ci(pattern: str) -> str:
    """Case-insensitive glob pattern (glob is case-sensitive off Windows)."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in pattern)


def find_drivers() -> list[str]:
    """Token DLLs on this PC: System32/SysWOW64 directly, Program Files folders up to 3 levels deep."""
    win = os.environ.get("SystemRoot", r"C:\Windows")
    roots = [(os.path.join(win, "System32"), 0), (os.path.join(win, "SysWOW64"), 0)]
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            roots.append((os.environ[var], 3))
    found: dict[str, str] = {}
    for root, depth in roots:
        for level in range(depth + 1):
            sub = os.path.join(root, *["*"] * level)
            for pat in PKCS11_DLLS:
                for p in glob.glob(os.path.join(sub, _ci(pat))):
                    found[os.path.normcase(p)] = p
    # 64-bit System32 first, then the rest
    return sorted(found.values(), key=lambda p: (os.path.basename(p).lower(), "syswow64" in p.lower()))


def _attr(name: x509.Name, oid) -> str:
    a = name.get_attributes_for_oid(oid)
    return a[0].value if a else ""


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def is_indian_ca(issuer: x509.Name) -> bool:
    text = _norm(_attr(issuer, NameOID.COMMON_NAME) + _attr(issuer, NameOID.ORGANIZATION_NAME))
    return any(k in text for k in CA_KEYWORDS)


def days_left(valid_to_iso: str) -> int:
    return (dt.date.fromisoformat(valid_to_iso) - dt.date.today()).days


def status_of(days: int) -> str:
    if days < 0:
        return "Expired"
    return "Expiring Soon" if days <= EXPIRING_DAYS else "Active"


def _is_ca_cert(cert: x509.Certificate) -> bool:
    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


def _to_dict(cert: x509.Certificate, source: str) -> dict:
    expiry = cert.not_valid_after_utc.date().isoformat()
    d = days_left(expiry)
    return {
        "holder_name": _attr(cert.subject, NameOID.COMMON_NAME),
        "issuer": _attr(cert.issuer, NameOID.COMMON_NAME),
        "serial_number": format(cert.serial_number, "X"),
        "issue_date": cert.not_valid_before_utc.date().isoformat(),
        "expiry_date": expiry,
        "days_remaining": d,
        "expiry_status": status_of(d),
        "source": source,  # "token" (physically plugged in) | "windows_store"
    }


def _scan_pkcs11(drivers: list[str]):
    """Return (certs, token_present, errors). Public read-only sessions: no PIN."""
    certs: list[dict] = []
    present = False
    errors: list[str] = []
    seen_names: set[str] = set()
    if drivers and PyKCS11 is None:
        errors.append("PyKCS11 is not installed (pip install PyKCS11).")
        return certs, present, errors
    for path in drivers:
        name = os.path.basename(path).lower()
        if name in seen_names:  # System32 and SysWOW64 copies: first (64-bit) wins unless it failed
            continue
        lib = PyKCS11.PyKCS11Lib()
        try:
            lib.load(path)
            slots = lib.getSlotList(tokenPresent=True)
            seen_names.add(name)
            for slot in slots:
                present = True
                session = lib.openSession(slot)  # read-only public session
                try:
                    objs = session.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
                    for o in objs:
                        der = bytes(session.getAttributeValue(o, [PyKCS11.CKA_VALUE])[0])
                        try:
                            cert = x509.load_der_x509_certificate(der)
                        except ValueError:
                            continue
                        if not _is_ca_cert(cert):
                            certs.append(_to_dict(cert, "token"))
                finally:
                    session.closeSession()
        except Exception as e:  # wrong bitness, driver refuses, slot busy, ...
            errors.append(f"{path}: {e}")
        finally:
            del lib  # finalizes the module so the next scan can re-initialize it
    return certs, present, errors


def _scan_windows_store(only_indian: bool = True) -> list[dict]:
    if not hasattr(ssl, "enum_certificates"):  # non-Windows
        return []
    out = []
    for der, encoding, _trust in ssl.enum_certificates("MY"):
        if encoding != "x509_asn":
            continue
        try:
            cert = x509.load_der_x509_certificate(der)
        except ValueError:
            continue
        if not only_indian:  # debug view: everything, flagged
            out.append({**_to_dict(cert, "windows_store"), "indian_ca": is_indian_ca(cert.issuer),
                        "is_ca_cert": _is_ca_cert(cert)})
        elif is_indian_ca(cert.issuer) and not _is_ca_cert(cert):
            out.append(_to_dict(cert, "windows_store"))
    return out


def scan() -> dict:
    """{'certificates': [...], 'message': str|None, 'drivers': [...], 'errors': [...]}"""
    drivers = find_drivers()
    token_certs, present, errors = _scan_pkcs11(drivers)
    merged: dict[str, dict] = {c["serial_number"]: c for c in token_certs}
    for c in _scan_windows_store():
        merged.setdefault(c["serial_number"], c)  # token copy wins
    certs = sorted(merged.values(), key=lambda c: (c["source"] != "token", c["expiry_date"]))

    message = None
    if not certs:
        if not drivers:
            message = ("No DSC token driver detected on this PC. Please open 'This PC' and install "
                       "the driver from the USB token's virtual CD drive.")
        elif errors:
            message = "Token driver found but could not be used: " + " | ".join(errors)
        elif not present:
            message = "Token driver is installed, but no DSC pen drive is currently plugged into a USB port."
        else:
            message = "A token is plugged in but no certificate could be read from it."
    return {"certificates": certs, "message": message, "drivers": drivers, "errors": errors}


def debug() -> dict:
    """Everything the scanner can see, for troubleshooting."""
    drivers = find_drivers()
    certs, present, errors = _scan_pkcs11(drivers)
    return {
        "python_bits": struct.calcsize("P") * 8,
        "pykcs11_installed": PyKCS11 is not None,
        "drivers_found": drivers,
        "pkcs11": {"token_present": present, "errors": errors, "certificates": certs},
        "windows_store_all": _scan_windows_store(only_indian=False),
    }


def read_dscs() -> list[dict]:
    return scan()["certificates"]


if __name__ == "__main__":
    assert status_of(-1) == "Expired" and status_of(30) == "Expiring Soon" and status_of(31) == "Active"
    r = scan()
    print("Drivers found:", r["drivers"] or "none")
    print("Errors:", r["errors"] or "none")
    for c in r["certificates"]:
        print(c)
    if r["message"]:
        print("MESSAGE:", r["message"])
