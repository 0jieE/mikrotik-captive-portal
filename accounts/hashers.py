from django.contrib.auth.hashers import PBKDF2PasswordHasher


class BulkPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """Cheaper PBKDF2 used ONLY when importing a masterlist (hundreds of users in one request).
    Django upgrades the hash to the default hasher automatically on the user's first successful login."""

    algorithm = "pbkdf2_sha256_bulk"
    iterations = 20_000
