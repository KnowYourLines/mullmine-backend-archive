import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class RoomConsumer(AsyncJsonWebsocketConsumer):
    pass
