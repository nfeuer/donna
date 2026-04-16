"""Authentication and authorization for the Donna REST API.

See docs/superpowers/specs/2026-04-14-api-auth-hardening-design.md.
"""

# Re-export the existing Firebase-JWT dependency so `from donna.api.auth
# import CurrentUser` keeps working for routes migrated before Task 17
# deletes the Firebase stub. The legacy module lives as a package
# submodule because `src/donna/api/auth.py` would otherwise be shadowed
# by this package on `import donna.api.auth`.
from donna.api.auth._firebase import CurrentUser, get_current_user_id

__all__ = ["CurrentUser", "get_current_user_id"]
