"""
Packet validation utilities for verifying packet integrity and security in the bot.
"""
import json
from typing import Dict, Any, Optional
from .bot_packet import BotPacket


class BotPacketValidator:
    """
    Utility class for validating bot packets.
    """
    
    @staticmethod
    def validate_packet(packet: BotPacket, secret_key: str = None,
                       allowed_providers: list = None) -> Dict[str, Any]:
        """
        Validate a packet for integrity and security.

        Args:
            packet: Packet to validate
            secret_key: Secret key for signature validation (if needed)
            allowed_providers: List of allowed packet providers

        Returns:
            Dict with validation results
        """
        result = {
            "valid": True,
            "errors": [],
            "warnings": []
        }

        # Validate headers
        if not packet.validate_headers():
            result["valid"] = False
            result["errors"].append("Invalid packet headers")

        # Validate timestamp
        if not packet.validate_timestamp():
            result["valid"] = False
            result["errors"].append("Packet timestamp is outside acceptable range")

        # Validate provider if allowed providers specified
        if allowed_providers and hasattr(packet, 'provider'):
            if packet.provider not in allowed_providers:
                result["valid"] = False
                result["errors"].append(f"Unauthorized packet provider: {packet.provider}")

        # Validate HMAC signature if secret_key is provided
        if secret_key:
            if not packet.signature:
                result["valid"] = False
                result["errors"].append("Packet is missing required signature")
            elif not packet.validate_signature(secret_key):
                result["valid"] = False
                result["errors"].append("Packet signature validation failed")

        return result
    
    @staticmethod
    def validate_packet_json(json_str: str, secret_key: str = None,
                            allowed_providers: list = None) -> Dict[str, Any]:
        """
        Validate a packet from JSON string.
        
        Args:
            json_str: JSON string representation of packet
            secret_key: Secret key for signature validation (if needed)
            allowed_providers: List of allowed packet providers
            
        Returns:
            Dict with validation results
        """
        try:
            packet_data = json.loads(json_str)
            packet = BotPacket.from_dict(packet_data)
            return BotPacketValidator.validate_packet(packet, secret_key, allowed_providers)
        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Failed to parse packet: {str(e)}"],
                "warnings": []
            }