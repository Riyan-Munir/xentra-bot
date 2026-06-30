"""
Packet factory for creating different types of packets in the bot.
"""
from typing import Any, Dict, Optional
from .bot_packet import BotPacket


class BotPacketFactory:
    """
    Factory class for creating different types of bot packets.
    """
    
    @staticmethod
    def create_packet(packet_type: str, data: Any = None, provider: str = "bot") -> BotPacket:
        """
        Create a packet of the specified type and auto-sign it.
        
        Args:
            packet_type: Type of packet to create
            data: Data to include in the packet
            provider: Provider of the packet
            
        Returns:
            BotPacket: Created and auto-signed packet instance
        """
        from config import WEBHOOK_SECRET
        packet = BotPacket(packet_type, data, provider)
        packet._generate_signature(WEBHOOK_SECRET)
        return packet
    
    @staticmethod
    def create_command_packet(command_data: Dict[str, Any], command_name: str) -> BotPacket:
        """
        Create a command-related packet.
        
        Args:
            command_data: Command data to include
            command_name: Name of the command
            
        Returns:
            BotPacket: Command packet instance
        """
        return BotPacket(
            packet_type=f"command_{command_name}",
            data=command_data,
            provider="bot"
        )
    
    @staticmethod
    def create_event_packet(event_data: Dict[str, Any], event_type: str) -> BotPacket:
        """
        Create an event-related packet.
        
        Args:
            event_data: Event data to include
            event_type: Type of event
            
        Returns:
            BotPacket: Event packet instance
        """
        return BotPacket(
            packet_type=f"event_{event_type}",
            data=event_data,
            provider="bot"
        )