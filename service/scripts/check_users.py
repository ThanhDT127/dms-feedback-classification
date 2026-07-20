"""List users in Azure AD via the refactored auth provider."""

from __future__ import annotations

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dms.auth import AuthProvider
from dms.settings import get_settings


def main() -> None:
    headers = AuthProvider(get_settings()).get_headers()

    print("=== Azure AD Users (first 20) ===\n")
    url = (
        "https://graph.microsoft.com/v1.0/users?$top=20&$select=displayName,mail,userPrincipalName"
    )
    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 200:
        users = resp.json().get("value", [])
        for user in users:
            name = user.get("displayName", "?")
            mail = user.get("mail", "(no mail)")
            upn = user.get("userPrincipalName", "?")
            print(f"  {name}")
            print(f"    mail: {mail}")
            print(f"    UPN:  {upn}")
            print()
    else:
        print(f"  Error {resp.status_code}: {resp.text[:300]}")
        print("\n  Note: App may need User.Read.All permission to list users.")
        print("  You can also manually check: Azure Portal -> Users")

    print("\n=== Check specific user: user@your-org.com ===")
    url2 = "https://graph.microsoft.com/v1.0/users/user@your-org.com?$select=displayName,mail,userPrincipalName"
    resp2 = requests.get(url2, headers=headers, timeout=10)
    if resp2.status_code == 200:
        user = resp2.json()
        print(
            f"  Found: {user.get('displayName')} / "
            f"mail={user.get('mail')} / UPN={user.get('userPrincipalName')}"
        )
    else:
        print(f"  Not found ({resp2.status_code}): {resp2.text[:200]}")


if __name__ == "__main__":
    main()
