"""Generate a self-signed TLS certificate for serving the OTA backend over HTTPS.

The ESP32 pins this certificate as its CA, so the SAN must contain the exact host
the device dials. Pass the host (IP or name) as the first argument; it is added as
an IP SAN when it parses as an address, otherwise as a DNS SAN. Writes
`tls_cert.pem` and `tls_key.pem` under backend/keys/.

    uv run python backend/scripts/generate_tls_cert.py 192.168.1.110
"""

from __future__ import annotations

import datetime
import ipaddress
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_settings  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def _san_for(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.110"
    settings = get_settings()
    settings.keys_dir.mkdir(parents=True, exist_ok=True)
    cert_path = settings.keys_dir / "tls_cert.pem"
    key_path = settings.keys_dir / "tls_key.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])

    san = [
        _san_for(host),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]

    # The device hard-codes its clock to ~2026-06-24, so keep the window around that.
    not_before = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
    not_after = datetime.datetime(2036, 6, 1, tzinfo=datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"Wrote {cert_path}")
    print(f"Wrote {key_path}  (SAN host: {host})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
