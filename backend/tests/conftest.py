import os

# Set DEBUG=true so the default SECRET_KEY is allowed during tests
os.environ.setdefault("DEBUG", "true")
