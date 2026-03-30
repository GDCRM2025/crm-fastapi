#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from pathlib import Path


def _b64url_nopad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera llaves VAPID (Web Push) para CRM GD.")
    ap.add_argument(
        "--out",
        default="data/vapid_private_key.pem",
        help="Ruta donde escribir la llave privada PEM (default: data/vapid_private_key.pem)",
    )
    ap.add_argument(
        "--subject",
        default="mailto:soporte@greendiamond.cl",
        help="VAPID subject (default: mailto:soporte@greendiamond.cl)",
    )
    args = ap.parse_args()

    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        from cryptography.hazmat.primitives.serialization import PublicFormat
    except Exception as e:
        print("ERROR: falta cryptography. Instala dependencias: pip install -r requirements.txt")
        print("Detalle:", e)
        return 2

    key = ec.generate_private_key(ec.SECP256R1())

    priv_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_raw = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64url = _b64url_nopad(pub_raw)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(priv_pem)

    print("\n# VAPID generado")
    print(f"# Private key PEM escrito en: {out_path}")
    print("# Public key (para el navegador / PushManager):")
    print(pub_b64url)
    print("\n# Exporta estas variables en el server (.env o Passenger):")
    print(f"export CRM_VAPID_PUBLIC_KEY='{pub_b64url}'")
    print(f"export CRM_VAPID_PRIVATE_KEY='{str(out_path)}'")
    print(f"export CRM_VAPID_SUBJECT='{args.subject}'")
    print("\n# Reinicia Passenger:")
    print("touch tmp/restart.txt\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

