from abc import ABC, abstractmethod

class IJWT(ABC):
    @abstractmethod
    def verify_token(self, token: str) -> dict:
        """Verify the JWT token and return the decoded data."""
        pass
    @abstractmethod
    def create_token(self, data: dict, expires_in: int) -> str:
        """Create a JWT token with the given data and expiration time."""
        pass