"""
Packet handler for bot to process incoming packets.
"""
import json
from typing import Dict, Any, Optional
from discord import Embed
from .bot_packet import BotPacket
from .validation import BotPacketValidator


class BotPacketHandler:
    """
    Handler for processing packets in the bot.
    """
    
    def __init__(self, secret_key: str = None):
        self.secret_key = secret_key
    
    def process_incoming_packet(self, packet_data: Dict[str, Any]) -> Embed:
        """
        Process an incoming packet from the backend.
        
        Args:
            packet_data: Raw packet data
            
        Returns:
            Embed: Discord embed with processing result
        """
        try:
            # Create packet from data
            packet = BotPacket.from_dict(packet_data)
            
            # Validate packet
            validation_result = BotPacketValidator.validate_packet(
                packet,
                self.secret_key,
                ["backend", "system"]
            )
            
            if not validation_result["valid"]:
                embed = Embed(
                    title="Packet Validation Error",
                    description="Failed to validate incoming packet",
                    color=0xFF0000
                )
                embed.add_field(
                    name="Errors",
                    value="\n".join(validation_result["errors"]),
                    inline=False
                )
                return embed
            
            # Process packet based on type
            packet_type = packet.packet_type
            if packet_type.startswith("command_"):
                return self._handle_command_packet(packet)
            elif packet_type.startswith("event_"):
                return self._handle_event_packet(packet)
            else:
                embed = Embed(
                    title="Unknown Packet Type",
                    description=f"Received packet of unknown type: {packet_type}",
                    color=0xFFFF00
                )
                return embed
                
        except Exception as e:
            embed = Embed(
                title="Packet Processing Error",
                description=f"Failed to process packet: {str(e)}",
                color=0xFF0000
            )
            return embed
    
    def _handle_command_packet(self, packet: BotPacket) -> Embed:
        """Handle command-related packet."""
        embed = Embed(
            title="Command Packet Received",
            description=f"Processing command: {packet.packet_type}",
            color=0x00FF00
        )
        embed.add_field(
            name="Data",
            value=str(packet.data),
            inline=False
        )
        return embed
    
    def _handle_event_packet(self, packet: BotPacket) -> Embed:
        """Handle event-related packet."""
        embed = Embed(
            title="Event Packet Received",
            description=f"Processing event: {packet.packet_type}",
            color=0x00FF00
        )
        embed.add_field(
            name="Data",
            value=str(packet.data),
            inline=False
        )
        return embed
    
    def create_response_packet(self, packet_type: str, data: Any,
                              provider: str = "bot") -> BotPacket:
        """
        Create a response packet.
        
        Args:
            packet_type: Type of response packet
            data: Data to include in response
            provider: Provider of the response
            
        Returns:
            BotPacket: Response packet
        """
        return BotPacket(packet_type, data, provider)