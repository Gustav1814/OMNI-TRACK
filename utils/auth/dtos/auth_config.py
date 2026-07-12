
class AuthConfig:
    import os
    JWT_SECRET: str = os.getenv("JWT_SECRET", "changeme123")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_SECONDS: int = int(os.getenv("JWT_EXPIRE_SECONDS", "300000000"))
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "navrox-admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "navrox-admin@@123")
    HARDCODED_JWT_TOKEN: str = os.getenv("HARDCODED_JWT_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJuYXZyb3gtYWRtaW4iLCJqdGkiOiI0MzQ2NmVhNC1jMTcwLTQ3ZTktYjgyZS0zZDQzNjI0ZWUwZDkiLCJleHAiOjIwNzUyMjAxMDh9.rrTGCEpL6PKAaTFzAIV4wy-HVs0kUEaOBfasZh_c7Es")