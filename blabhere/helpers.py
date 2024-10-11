from blabhere.models import Room, Message, User


def get_num_room_members(room):
    return room.members.all().count()


def is_room_creator(room, user):
    return room.creator.username == user.username


def update_member_limit(new_limit, room, user):
    if room.creator.username == user.username:
        room.max_num_members = new_limit
        room.save()
    return room


def check_room_full(room_id):
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        return (
            room.max_num_members and room.max_num_members < room.members.all().count()
        )


def get_room(room_id):
    room = Room.objects.get(id=room_id)
    return room


def initialize_room(room_id, user):
    room, created = Room.objects.get_or_create(
        id=room_id,
        defaults={"display_name": "A new room name", "creator": user},
    )
    return room


def get_user(username):
    user = User.objects.get(username=username)
    return user


def get_all_member_display_names(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("display_name")
    return [member["display_name"] for member in members]


def add_user_to_room(user, room):
    was_added = False
    if user not in room.members.all():
        room.members.add(user)
        was_added = True
    member_display_names = get_all_member_display_names(room.id)
    return member_display_names, was_added


def update_room_name(name, room):
    room.display_name = name
    room.save()


def get_initial_messages(room):
    messages = [
        {
            "creator_username": msg.creator.username,
            "creator_display_name": msg.creator.display_name,
            "content": msg.content,
            "created_at": msg.created_at.timestamp(),
            "id": str(msg.id),
        }
        for msg in room.message_set.order_by("-created_at")[:10][::-1]
    ]
    return messages


def get_refreshed_messages(room, oldest_message_timestamp):
    if oldest_message_timestamp:
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(
                created_at__gte=oldest_message_timestamp
            ).order_by("-created_at")[::-1]
        ]
    else:
        messages = []
    return messages


def create_new_message(content, room, creator):

    new_message = Message.objects.create(creator=creator, room=room, content=content)
    return {
        "creator_username": new_message.creator.username,
        "creator_display_name": new_message.creator.display_name,
        "content": new_message.content,
        "created_at": new_message.created_at.timestamp(),
        "id": str(new_message.id),
    }


def get_prev_messages(oldest_msg_id, room):
    messages = []
    oldest_msg = Message.objects.filter(id=oldest_msg_id, room=room)
    if oldest_msg.exists():
        oldest_msg = oldest_msg.first()
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(
                created_at__lt=oldest_msg.created_at
            ).order_by("-created_at")[:10][::-1]
        ]
    return messages


def change_user_display_name(user, new_name):
    user.display_name = new_name
    user.save()
    rooms_to_refresh = [str(room["id"]) for room in user.room_set.all().values()]
    return new_name, rooms_to_refresh
