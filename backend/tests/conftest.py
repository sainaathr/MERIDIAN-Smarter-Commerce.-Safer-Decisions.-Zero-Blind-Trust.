import os
import pytest

# Configure a cryptographically secure, non-placeholder test key for the automated test harness
os.environ.setdefault("SECRET_KEY", "meridian-test-harness-secure-random-secret-key-64-bytes-0123456789abcdef")
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("APP_ENV", "development")
