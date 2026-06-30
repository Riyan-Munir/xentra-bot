"""
Bot packet template with security headers, timestamps, and validation.
"""
import hashlib
import hmac
import json
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import secrets


class BotPacket:
    """
    Bot packet template with security features including:
    - Timestamps
    - Security headers
    - Data validation
    - Signature verification via HMAC-SHA256
    """
    
    def __init__(self, packet_type: str, data: Any = None, provider: str = "bot"):
        self.packet_type = packet_type
        self.provider = provider
        self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.data = data or {}
        self.version = "1.0"
        self.nonce = secrets.token_hex(16)
        self.signature = ""
    
    def _get_packet_data_for_signing(self) -> Dict[str, Any]:
        """Return the packet data dict used for signature generation/validation."""
        return {
            "type": self.packet_type,
            "provider": self.provider,
            "timestamp": self.timestamp,
            "version": self.version,
            "nonce": self.nonce,
            "data": self.data
        }
    
    def _serialize_for_signing(self) -> str:
        """Serialize packet data deterministically for HMAC signing."""
        return json.dumps(
            self._get_packet_data_for_signing(),
            sort_keys=True,
            ensure_ascii=True,
            default=str,
            separators=(',', ':')
        )
    
    def _compute_signature(self, secret_key: str = "") -> str:
        """Compute an HMAC-SHA256 signature for the packet WITHOUT mutating self.signature."""
        packet_string = self._serialize_for_signing()
        return hmac.HMAC(
            secret_key.encode(),
            packet_string.encode(),
            hashlib.sha256
        ).hexdigest()

    def _generate_signature(self, secret_key: str = "") -> str:
        """Generate an HMAC-SHA256 signature for the packet (mutates self.signature)."""
        signature = self._compute_signature(secret_key)
        self.signature = signature
        return self.signature
    
    def validate_signature(self, secret_key: str) -> bool:
        """Validate the packet signature using HMAC-SHA256 with the provided secret key.

        NOTE: This method computes the expected signature WITHOUT mutating self.signature,
        so it correctly compares the original packet signature against the computed one.
        """
        expected = self._compute_signature(secret_key)
        return hmac.compare_digest(self.signature, expected)
    
    def validate_timestamp(self, max_age: int = 300) -> bool:
        """Validate that the packet timestamp is within acceptable range."""
        packet_time = datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
        if packet_time.tzinfo is None:
            packet_time = packet_time.replace(tzinfo=timezone.utc)
        current_time = datetime.now(timezone.utc)
        time_diff = (current_time - packet_time).total_seconds()
        return abs(time_diff) <= max_age
    
    def validate_headers(self) -> bool:
        """Validate all packet headers and metadata."""
        required_fields = ["packet_type", "provider", "timestamp", "version", "nonce"]
        for field in required_fields:
            if not getattr(self, field, None):
                return False
        if not self.validate_timestamp():
            return False
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert packet to dictionary format."""
        return {
            "type": self.packet_type,
            "provider": self.provider,
            "timestamp": self.timestamp,
            "version": self.version,
            "nonce": self.nonce,
            "signature": self.signature,
            "data": self.data
        }
    
    def to_json(self) -> str:
        """Convert packet to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BotPacket':
        """Create packet from dictionary."""
        packet = cls(
            packet_type=data.get("type"),
            data=data.get("data"),
            provider=data.get("provider", "bot")
        )
        packet.timestamp = data.get("timestamp", packet.timestamp)
        packet.version = data.get("version", packet.version)
        packet.nonce = data.get("nonce", packet.nonce)
        packet.signature = data.get("signature", "")
        return packet
    
    @classmethod
    def from_json(cls, json_str: str) -> 'BotPacket':
        """Create packet from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
