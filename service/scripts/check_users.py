"""
Quick check: List users in Azure AD tenant that have mailboxes.
Helps identify the correct sender email address.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from auth import get_headers

def main():
    headers = get_headers()

    # Try listing users
    print("=== Azure AD Users (first 20) ===\n")
    url = "https://graph.microsoft.com/v1.0/users?$top=20&$select=displayName,mail,userPrincipalName"
    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 200:
        users = resp.json().get("value", [])
        for u in users:
            name = u.get("displayName", "?")
            mail = u.get("mail", "(no mail)")
            upn = u.get("userPrincipalName", "?")
            print(f"  {name}")
            print(f"    mail: {mail}")
            print(f"    UPN:  {upn}")
            print()
    else:
        print(f"  Error {resp.status_code}: {resp.text[:300]}")
        print("\n  Note: App may need User.Read.All permission to list users.")
        print("  You can also manually check: Azure Portal → Users")

    # Try checking specific user
    print("\n=== Check specific user: user@your-org.com ===")
    url2 = "https://graph.microsoft.com/v1.0/users/user@your-org.com?$select=displayName,mail,userPrincipalName"
    resp2 = requests.get(url2, headers=headers, timeout=10)
    if resp2.status_code == 200:
        u = resp2.json()
        print(f"  Found: {u.get('displayName')} / mail={u.get('mail')} / UPN={u.get('userPrincipalName')}")
    else:
        print(f"  Not found ({resp2.status_code}): {resp2.text[:200]}")

if __name__ == "__main__":
    main()
