import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class RoomConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        self.room_id = None
        self.user = None

    async def connect(self):
        await self.accept()
        self.user = self.scope["user"]

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(str(self.room_id), self.channel_name)

    async def initialize_room(self):
        await self.channel_layer.group_add(self.room_id, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("command") == "connect":
            self.room_id = content.get("room")
            await self.initialize_room()
