import logging

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import F, Count, Q, Case, When, FloatField
from django.db.models.functions import Cast
from django.db.models.lookups import GreaterThan
from firebase_admin.auth import delete_user as delete_firebase_user

from blabhere.models import Room, Message, User, Conversation, ChatTopic, ReportedChat

FULL_ROOM_NUM_MEMBERS = 10


def chat_partner_is_online(room_id, user):
    room = Room.objects.filter(id=room_id).first()
    other_member = room.members.exclude(id=user.id).first()
    if other_member:
        return other_member.is_online


def get_all_room_ids(user):
    room_ids = list(user.room_set.all().values_list("id", flat=True))
    return room_ids


def set_online(username):
    User.objects.filter(username=username).update(is_online=True)


def set_offline(username):
    User.objects.filter(username=username).update(is_online=False)


def delete_user(user):
    delete_firebase_user(user.username)
    user.delete()


def block_other_user(room_id, user):
    other_member = (
        Room.objects.filter(id=room_id)
        .annotate(
            other_member=ArrayAgg(
                "members",
                filter=~Q(members__id=user.id),
                distinct=True,
            )
        )[0]
        .other_member[0]
    )
    user.blocked_users.add(other_member)


def log_reported_chat(room, reporter, reported_user_id):
    messages = list(
        room.message_set.all().values_list("content", flat=True).order_by("created_at")
    )
    reported = User.objects.get(id=reported_user_id)
    ReportedChat.objects.update_or_create(
        reporter=reporter, reported=reported, defaults={"messages": messages}
    )


def report_other_user(room_id, user):
    room = Room.objects.filter(id=room_id)
    other_member = room.annotate(
        other_member=ArrayAgg(
            "members",
            filter=~Q(members__id=user.id),
            distinct=True,
        )
    )[0].other_member[0]
    user.reported_users.add(other_member)
    log_reported_chat(room.first(), user, other_member)


def get_user_agreed_terms(username):
    user = User.objects.get(username=username)
    return user.agreed_terms_and_privacy


def agree_terms(username):
    User.objects.filter(username=username).update(agreed_terms_and_privacy=True)


def get_user_topics(username):
    user = User.objects.get(username=username)
    return [topic.name for topic in user.chat_topics.all()]


def remove_topic(user, topic):
    removed = False
    topic = ChatTopic.objects.filter(name=topic)
    if topic.exists():
        user.chat_topics.remove(topic.first())
        removed = True
    return removed


def save_topic(user, topic):
    added = False
    new_topic, created = ChatTopic.objects.get_or_create(name=topic)
    if not user.chat_topics.filter(id=new_topic.id).exists():
        user.chat_topics.add(new_topic)
        added = True
    return added


def leave_room(user, room_id):
    room_to_leave = Room.objects.get(id=room_id)
    if user in room_to_leave.members.all():
        room_to_leave.delete()


def read_unread_conversation(room_id, user):
    room = Room.objects.get(id=room_id)
    conversation = Conversation.objects.get(participant=user, room=room)
    if not conversation.read:
        Conversation.objects.filter(id=conversation.id).update(read=True)


def get_user_conversations(username):
    user = User.objects.get(username=username)
    conversations = list(
        user.conversation_set.annotate(
            other_members=ArrayAgg(
                "room__members__display_name",
                filter=~Q(room__members__display_name=user.display_name),
                distinct=True,
            )
        )
        .annotate(
            is_online=ArrayAgg(
                "room__members__is_online",
                filter=~Q(room__members__display_name=user.display_name),
                distinct=True,
            )
        )
        .values(
            "room__id",
            "read",
            "latest_message__creator__display_name",
            "latest_message__content",
            "latest_message__created_at",
            "other_members",
            "is_online",
            "created_at",
        )
        .order_by(
            "read", F("latest_message__created_at").desc(nulls_last=True), "-created_at"
        )
    )
    for conversation in conversations:
        conversation["room__id"] = str(conversation["room__id"])
        conversation["created_at"] = conversation["created_at"].timestamp()
        if conversation["latest_message__created_at"]:
            conversation["latest_message__created_at"] = conversation[
                "latest_message__created_at"
            ].timestamp()
    return conversations


def check_room_full(room_id, user):
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        is_member = room.members.filter(username=user.username).exists()
        return room.members.all().count() >= FULL_ROOM_NUM_MEMBERS and not is_member


def get_room(room_id):
    try:
        room = Room.objects.get(id=room_id)
        return room
    except ObjectDoesNotExist:
        logging.error(f"Room id {room_id} does not exist")


def get_most_chatted_users(user, exclude_room_ids=None):
    num_members = Count("members", distinct=True)
    num_messages = Count("message", distinct=True)
    num_your_messages = Count("message", filter=Q(message__creator=user), distinct=True)
    num_not_your_messages = Count(
        "message", filter=~Q(message__creator=user), distinct=True
    )
    chattiness_score = Case(
        When(
            GreaterThan(F("num_your_messages"), 0)
            & GreaterThan(F("num_not_your_messages"), 0),
            then=Cast(F("num_messages"), FloatField())
            * Cast(F("num_your_messages"), FloatField())
            / Cast(F("num_not_your_messages"), FloatField()),
        ),
        default=0,
        output_field=FloatField(),
    )
    other_members_ids = ArrayAgg(
        "members__id",
        filter=~Q(members__id=user.id),
        distinct=True,
    )
    your_chattiest_rooms = (
        Room.objects.annotate(num_members=num_members)
        .annotate(num_messages=num_messages)
        .annotate(num_your_messages=num_your_messages)
        .annotate(num_not_your_messages=num_not_your_messages)
        .annotate(num_not_your_messages=num_not_your_messages)
        .annotate(chattiness_score=chattiness_score)
        .annotate(other_members_ids=other_members_ids)
        .filter(members=user, num_members__lte=FULL_ROOM_NUM_MEMBERS)
        .order_by("-chattiness_score")
        .values("other_members_ids")
    )
    if exclude_room_ids:
        your_chattiest_rooms = your_chattiest_rooms.exclude(id__in=exclude_room_ids)
    members_ids = set()
    for room in your_chattiest_rooms[:5]:
        for member_id in room["other_members_ids"]:
            members_ids.add(member_id)
    return User.objects.filter(id__in=members_ids)


def get_most_chatted_users_of_most_chatted_users(user):
    top_most_chatted_users = get_most_chatted_users(user)
    exclude_room_ids = user.room_set.all().values_list("id", flat=True)
    users = User.objects.none()
    for top_user in top_most_chatted_users:
        top_user_most_chatted_users = get_most_chatted_users(
            top_user, exclude_room_ids=exclude_room_ids
        )
        if not users:
            users = top_user_most_chatted_users
        else:
            users.union(top_user_most_chatted_users)
    return users


def get_waiting_rooms(user):
    blocked_users_ids = user.blocked_users.all().values_list("id", flat=True)
    num_members = Count("members", distinct=True)
    num_members_online = Count(
        "members", filter=Q(members__is_online=True), distinct=True
    )
    waiting_rooms = (
        Room.objects.all()
        .annotate(num_members=num_members)
        .annotate(num_members_online=num_members_online)
        .filter(num_members__lt=FULL_ROOM_NUM_MEMBERS)
        .exclude(members__id__in=blocked_users_ids)
        .exclude(members__id=user.id, num_members__gt=1)
        .distinct()
    )
    return waiting_rooms


def get_same_topics_users(user):
    topics_ids = user.chat_topics.all().values_list("id", flat=True)
    user_rooms_members_ids = (
        User.objects.filter(room__in=user.room_set.all())
        .values_list("id", flat=True)
        .distinct()
    )
    users = (
        User.objects.filter(chat_topics__id__in=topics_ids)
        .exclude(
            id__in=user_rooms_members_ids,
        )
        .distinct()
    )
    return users


def find_waiting_room(user):
    waiting_rooms = get_waiting_rooms(user)
    user_rooms_members = User.objects.filter(room__in=user.room_set.all()).distinct()
    other_users_waiting_rooms = (
        waiting_rooms.exclude(members__in=user_rooms_members)
        .distinct()
        .order_by("-created_at", "-num_members_online")
    )
    your_own_waiting_rooms = waiting_rooms.filter(members=user).order_by("-created_at")
    most_chatted_users = get_most_chatted_users_of_most_chatted_users(user)
    most_chatted_waiting_rooms = (
        waiting_rooms.filter(members__in=most_chatted_users)
        .distinct()
        .order_by("-created_at", "-num_members_online")
    )
    same_topics_users = get_same_topics_users(user)
    same_topics_waiting_rooms = (
        waiting_rooms.filter(members__in=same_topics_users)
        .distinct()
        .order_by("-created_at", "-num_members_online")
    )
    if most_chatted_waiting_rooms.exists() and your_own_waiting_rooms.exists():
        most_chatted_waiting_room = most_chatted_waiting_rooms.first()
        your_own_waiting_rooms.delete()
        return most_chatted_waiting_room
    elif most_chatted_waiting_rooms.exists():
        return most_chatted_waiting_rooms.first()
    elif same_topics_waiting_rooms.exists() and your_own_waiting_rooms.exists():
        same_topics_waiting_room = same_topics_waiting_rooms.first()
        your_own_waiting_rooms.delete()
        return same_topics_waiting_room
    elif same_topics_waiting_rooms.exists():
        return same_topics_waiting_rooms.first()
    elif other_users_waiting_rooms.exists() and your_own_waiting_rooms.exists():
        other_user_waiting_room = other_users_waiting_rooms.first()
        your_own_waiting_rooms.delete()
        return other_user_waiting_room
    elif other_users_waiting_rooms.exists():
        return other_users_waiting_rooms.first()
    elif your_own_waiting_rooms.exists():
        return your_own_waiting_rooms.first()
    else:
        return Room.objects.create()


def initialize_room(room_id, user):
    if user.is_verified:
        if room_id:
            try:
                room = Room.objects.get(id=room_id)
                if 1 < room.members.count() <= FULL_ROOM_NUM_MEMBERS:
                    return room
            except ObjectDoesNotExist:
                return None
        return find_waiting_room(user)


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


def get_all_member_usernames(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("username")
    return [member["username"] for member in members]


def add_user_to_room(user, room):
    was_added = False
    latest_message = room.message_set.order_by("-created_at").first()
    if user not in room.members.all():
        room.members.add(user)
        Conversation.objects.create(
            participant=user, room=room, latest_message=latest_message, read=True
        )
        was_added = True
    member_display_names = get_all_member_display_names(room.id)
    return member_display_names, was_added


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
    update_conversations_for_new_message(room, new_message)
    return {
        "creator_username": new_message.creator.username,
        "creator_display_name": new_message.creator.display_name,
        "content": new_message.content,
        "created_at": new_message.created_at.timestamp(),
        "id": str(new_message.id),
    }


def update_conversations_for_new_message(room, message):
    for user in room.members.all():
        conversation = Conversation.objects.get(participant=user, room=room)
        Conversation.objects.filter(id=conversation.id).update(
            latest_message=message, read=user == message.creator
        )


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
    rooms_to_refresh = [str(room["id"]) for room in user.room_set.all().values()]
    users_to_refresh = [
        str(conversation["participant__username"])
        for conversation in Conversation.objects.filter(
            latest_message__creator=user
        ).values("participant__username")
    ]
    try:
        User.objects.filter(id=user.id).update(display_name=new_name)
        return True, new_name, rooms_to_refresh, users_to_refresh
    except IntegrityError:
        return False, new_name, rooms_to_refresh, users_to_refresh
